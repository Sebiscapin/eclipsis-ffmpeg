/*
 * AAC decoder using the Microsoft AAC decoder MFT (Media Foundation)
 *
 * This file is part of FFmpeg.
 *
 * FFmpeg is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * FFmpeg is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with FFmpeg; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
 */

/**
 * @file
 * AAC decoding through the AAC decoder that ships with Windows
 * (CLSID_MSAACDecMFT, msauddecmft.dll), so that no AAC decoder needs to be
 * compiled into the binary.
 *
 * The MFT returns one frame per input packet, one packet late, and gives each
 * frame the time of its packet. The properties of every packet sent to it
 * (pts, duration and the skip samples side data that the MP4 demuxer derives
 * from the edit list) are queued and attached to the frame made from that
 * packet: the generic code then trims the encoder priming exactly as it does
 * with the native decoder (FF_CODEC_CAP_SETS_FRAME_PROPS, so that the
 * properties of the last packet read are not used instead).
 */

#define COBJMACROS
#if !defined(_WIN32_WINNT) || _WIN32_WINNT < 0x0602
#undef _WIN32_WINNT
#define _WIN32_WINNT 0x0602
#endif

#include "config.h"

#include "libavutil/channel_layout.h"
#include "libavutil/common.h"
#include "libavutil/fifo.h"
#include "libavutil/intreadwrite.h"
#include "libavutil/mem.h"
#include "avcodec.h"
#include "codec_internal.h"
#include "decode.h"
#include "mf_utils.h"
#include "mpeg4audio.h"
#include "compat/w32dlfcn.h"

// CLSID_MSAACDecMFT: the Microsoft AAC decoder, not whatever MFT for AAC has
// the highest merit on the machine.
DEFINE_GUID(ff_CLSID_MSAACDecMFT, 0x32d186a7, 0x218f, 0x4c75,
            0x88, 0x76, 0xdd, 0x77, 0x27, 0x3a, 0x89, 0x99);

// Largest number of output format changes accepted in a row.
#define MAX_STREAM_CHANGES 4

typedef struct MFDecContext {
    HMODULE library;
    MFFunctions functions;
    int com_initialized;
    int mf_started;
    IMFTransform *mft;
    DWORD in_stream_id, out_stream_id;
    MFT_INPUT_STREAM_INFO in_info;
    MFT_OUTPUT_STREAM_INFO out_info;
    int out_stream_provides_samples;
    int bytes_per_frame;
    int stream_started;
    int draining;
    int64_t next_time;
    AVPacket *pkt;
    AVFifo *props;              ///< AVPacket*, properties of the packets given to the MFT
} MFDecContext;

static void mfdec_drop_props(MFDecContext *c)
{
    AVPacket *props;

    if (av_fifo_read(c->props, &props, 1) >= 0)
        av_packet_free(&props);
}

static void mfdec_drop_all_props(MFDecContext *c)
{
    AVPacket *props;

    while (av_fifo_read(c->props, &props, 1) >= 0)
        av_packet_free(&props);
}

static int mfdec_load_library(AVCodecContext *avctx)
{
    MFDecContext *c = avctx->priv_data;

#if !HAVE_UWP
    c->library = dlopen("mfplat.dll", 0);
    if (!c->library) {
        av_log(avctx, AV_LOG_ERROR, "DLL mfplat.dll failed to open "
               "(Windows N editions need the Media Feature Pack)\n");
        return AVERROR_UNKNOWN;
    }
#define LOAD_MF_FUNCTION(name)                                              \
    c->functions.name = (void *)dlsym(c->library, #name);                   \
    if (!c->functions.name) {                                               \
        av_log(avctx, AV_LOG_ERROR, "DLL mfplat.dll failed to find "        \
               "function " #name "\n");                                     \
        return AVERROR_UNKNOWN;                                             \
    }
#else
#define LOAD_MF_FUNCTION(name) c->functions.name = name;
#endif

    LOAD_MF_FUNCTION(MFStartup);
    LOAD_MF_FUNCTION(MFShutdown);
    LOAD_MF_FUNCTION(MFCreateAlignedMemoryBuffer);
    LOAD_MF_FUNCTION(MFCreateSample);
    LOAD_MF_FUNCTION(MFCreateMediaType);
#undef LOAD_MF_FUNCTION

    return 0;
}

static int mfdec_set_input_type(AVCodecContext *avctx, int sample_rate,
                                int channels, int payload)
{
    MFDecContext *c = avctx->priv_data;
    int user_size = 12 + (payload == 0 ? avctx->extradata_size : 0);
    IMFMediaType *type;
    uint8_t *user_data;
    HRESULT hr;
    int ret = 0;

    hr = c->functions.MFCreateMediaType(&type);
    if (FAILED(hr))
        return AVERROR(ENOMEM);

    user_data = av_mallocz(user_size);
    if (!user_data) {
        IMFMediaType_Release(type);
        return AVERROR(ENOMEM);
    }
    // The part of HEAACWAVEINFO that follows WAVEFORMATEX (wPayloadType,
    // wAudioProfileLevelIndication, wStructType, wReserved1, dwReserved2),
    // then the AudioSpecificConfig for raw payloads.
    AV_WL16(user_data,     payload);
    AV_WL16(user_data + 2, 0xFE);
    if (payload == 0)
        memcpy(user_data + 12, avctx->extradata, avctx->extradata_size);

    IMFMediaType_SetGUID(type, &MF_MT_MAJOR_TYPE, &MFMediaType_Audio);
    IMFMediaType_SetGUID(type, &MF_MT_SUBTYPE, &MFAudioFormat_AAC);
    IMFMediaType_SetUINT32(type, &MF_MT_AUDIO_SAMPLES_PER_SECOND, sample_rate);
    IMFMediaType_SetUINT32(type, &MF_MT_AUDIO_NUM_CHANNELS, channels);
    IMFMediaType_SetUINT32(type, &MF_MT_AAC_PAYLOAD_TYPE, payload);
    IMFMediaType_SetUINT32(type, &MF_MT_AAC_AUDIO_PROFILE_LEVEL_INDICATION, 0xFE);
    IMFMediaType_SetBlob(type, &MF_MT_USER_DATA, user_data, user_size);
    av_free(user_data);

    hr = IMFTransform_SetInputType(c->mft, c->in_stream_id, type, 0);
    if (FAILED(hr)) {
        av_log(avctx, AV_LOG_VERBOSE, "input type refused (%d Hz, %d channels, "
               "payload %d): %s\n", sample_rate, channels, payload, ff_hr_str(hr));
        ret = AVERROR_EXTERNAL;
    }
    IMFMediaType_Release(type);
    return ret;
}

static int mfdec_set_output_type(AVCodecContext *avctx)
{
    MFDecContext *c = avctx->priv_data;
    enum AVSampleFormat sample_fmt = AV_SAMPLE_FMT_NONE;
    UINT32 sample_rate = 0, channels = 0, mask = 0;
    IMFMediaType *chosen = NULL;
    HRESULT hr;
    int ret = 0;

    // 32 bit float like the native decoder; 16 bit only if there is no float.
    for (int n = 0; ; n++) {
        enum AVSampleFormat fmt;
        IMFMediaType *type;

        hr = IMFTransform_GetOutputAvailableType(c->mft, c->out_stream_id, n, &type);
        if (FAILED(hr))
            break;
        fmt = ff_media_type_to_sample_fmt((IMFAttributes *)type);
        if (fmt == AV_SAMPLE_FMT_FLT ||
            (fmt == AV_SAMPLE_FMT_S16 && sample_fmt == AV_SAMPLE_FMT_NONE)) {
            if (chosen)
                IMFMediaType_Release(chosen);
            chosen     = type;
            sample_fmt = fmt;
            if (fmt == AV_SAMPLE_FMT_FLT)
                break;
        } else {
            IMFMediaType_Release(type);
        }
    }
    if (!chosen) {
        av_log(avctx, AV_LOG_ERROR, "the decoder offers neither float nor 16 bit output\n");
        return AVERROR_EXTERNAL;
    }

    hr = IMFTransform_SetOutputType(c->mft, c->out_stream_id, chosen, 0);
    if (!FAILED(hr)) {
        IMFMediaType_GetUINT32(chosen, &MF_MT_AUDIO_SAMPLES_PER_SECOND, &sample_rate);
        IMFMediaType_GetUINT32(chosen, &MF_MT_AUDIO_NUM_CHANNELS, &channels);
        if (FAILED(IMFMediaType_GetUINT32(chosen, &MF_MT_AUDIO_CHANNEL_MASK, &mask)))
            mask = 0;
    }
    IMFMediaType_Release(chosen);
    if (FAILED(hr)) {
        av_log(avctx, AV_LOG_ERROR, "could not set the output type: %s\n", ff_hr_str(hr));
        return AVERROR_EXTERNAL;
    }
    if (!sample_rate || !channels || sample_rate > INT_MAX || channels > 64) {
        av_log(avctx, AV_LOG_ERROR, "output type without a usable sample rate or "
               "channel count\n");
        return AVERROR_EXTERNAL;
    }

    avctx->sample_fmt  = sample_fmt;
    avctx->sample_rate = sample_rate;
    av_channel_layout_uninit(&avctx->ch_layout);
    if (mask && (UINT32)av_popcount(mask) == channels)
        ret = av_channel_layout_from_mask(&avctx->ch_layout, mask);
    else
        av_channel_layout_default(&avctx->ch_layout, channels);
    if (ret < 0)
        return ret;
    c->bytes_per_frame = av_get_bytes_per_sample(sample_fmt) * channels;

    hr = IMFTransform_GetInputStreamInfo(c->mft, c->in_stream_id, &c->in_info);
    if (FAILED(hr))
        return AVERROR_EXTERNAL;
    hr = IMFTransform_GetOutputStreamInfo(c->mft, c->out_stream_id, &c->out_info);
    if (FAILED(hr))
        return AVERROR_EXTERNAL;
    c->out_stream_provides_samples =
        (c->out_info.dwFlags & MFT_OUTPUT_STREAM_PROVIDES_SAMPLES) ||
        (c->out_info.dwFlags & MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES);
    // Room for the largest frames (2048 samples with SBR) if the MFT says nothing.
    if (!c->out_info.cbSize)
        c->out_info.cbSize = 4 * 2048 * c->bytes_per_frame;

    av_log(avctx, AV_LOG_VERBOSE, "output: %u Hz, %u channels, %s\n",
           (unsigned)sample_rate, (unsigned)channels, av_get_sample_fmt_name(sample_fmt));
    return 0;
}

static av_cold int mfdec_init(AVCodecContext *avctx)
{
    MFDecContext *c = avctx->priv_data;
    MPEG4AudioConfig m4ac = { 0 };
    int payload = avctx->extradata_size >= 2 ? 0 : 1; // raw with config, or ADTS
    int rates[4], chans[2];
    HRESULT hr;
    int ret;

    c->pkt   = av_packet_alloc();
    c->props = av_fifo_alloc2(8, sizeof(AVPacket *), AV_FIFO_FLAG_AUTO_GROW);
    if (!c->pkt || !c->props)
        return AVERROR(ENOMEM);

    if ((ret = mfdec_load_library(avctx)) < 0)
        return ret;

    // The decoder is used synchronously, so an apartment set up by the caller
    // (RPC_E_CHANGED_MODE) is fine too.
    hr = CoInitializeEx(NULL, COINIT_MULTITHREADED);
    if (FAILED(hr) && hr != RPC_E_CHANGED_MODE) {
        av_log(avctx, AV_LOG_ERROR, "could not initialize COM\n");
        return AVERROR(ENOSYS);
    }
    c->com_initialized = SUCCEEDED(hr);

    hr = c->functions.MFStartup(MF_VERSION, MFSTARTUP_FULL);
    if (FAILED(hr)) {
        av_log(avctx, AV_LOG_ERROR, "could not initialize MediaFoundation\n");
        return AVERROR(ENOSYS);
    }
    c->mf_started = 1;

    hr = CoCreateInstance(&ff_CLSID_MSAACDecMFT, NULL, CLSCTX_INPROC_SERVER,
                          &IID_IMFTransform, (void **)&c->mft);
    if (FAILED(hr)) {
        av_log(avctx, AV_LOG_ERROR, "the Windows AAC decoder is not available "
               "(Windows N editions need the Media Feature Pack): %s\n", ff_hr_str(hr));
        return AVERROR_DECODER_NOT_FOUND;
    }

    hr = IMFTransform_GetStreamIDs(c->mft, 1, &c->in_stream_id, 1, &c->out_stream_id);
    if (hr == E_NOTIMPL) {
        c->in_stream_id = c->out_stream_id = 0;
    } else if (FAILED(hr)) {
        av_log(avctx, AV_LOG_ERROR, "could not get the stream IDs: %s\n", ff_hr_str(hr));
        return AVERROR_EXTERNAL;
    }

    if (payload == 0 &&
        avpriv_mpeg4audio_get_config2(&m4ac, avctx->extradata, avctx->extradata_size,
                                      1, avctx) < 0)
        memset(&m4ac, 0, sizeof(m4ac));

    // What the container says first, then the core of the stream, then the
    // rate with SBR: the MFT is strict about how HE-AAC is signalled.
    rates[0] = avctx->sample_rate;
    rates[1] = m4ac.sample_rate;
    rates[2] = m4ac.ext_sample_rate;
    rates[3] = 2 * m4ac.sample_rate;
    chans[0] = avctx->ch_layout.nb_channels;
    chans[1] = m4ac.channels;
    ret = AVERROR_EXTERNAL;
    for (int i = 0; i < FF_ARRAY_ELEMS(rates) && ret < 0; i++)
        for (int j = 0; j < FF_ARRAY_ELEMS(chans) && ret < 0; j++)
            if (rates[i] > 0 && chans[j] > 0)
                ret = mfdec_set_input_type(avctx, rates[i], chans[j], payload);
    if (ret < 0) {
        av_log(avctx, AV_LOG_ERROR, "the Windows AAC decoder does not accept this stream\n");
        return ret;
    }

    return mfdec_set_output_type(avctx);
}

static int mfdec_send_packet(AVCodecContext *avctx, const AVPacket *pkt)
{
    MFDecContext *c = avctx->priv_data;
    int64_t duration = 0;
    IMFSample *sample;
    AVPacket *props;
    HRESULT hr;
    int ret;

    sample = ff_create_memory_sample(&c->functions, pkt->data, pkt->size,
                                     c->in_info.cbAlignment);
    if (!sample)
        return AVERROR(ENOMEM);

    // The MFT wants timestamps, but the frames take the properties of their
    // packets (see mfdec_sample_to_frame): a running clock in 100 ns units is
    // enough and avoids the negative times of the priming packets.
    if (pkt->duration > 0 && avctx->pkt_timebase.num > 0 && avctx->pkt_timebase.den > 0)
        duration = av_rescale_q(pkt->duration, avctx->pkt_timebase,
                                (AVRational){ 1, 10000000 });
    IMFSample_SetSampleTime(sample, c->next_time);
    if (duration > 0)
        IMFSample_SetSampleDuration(sample, duration);
    c->next_time += FFMAX(duration, 1);

    props = av_packet_alloc();
    if (!props) {
        IMFSample_Release(sample);
        return AVERROR(ENOMEM);
    }
    ret = av_packet_copy_props(props, pkt);
    if (ret < 0) {
        av_packet_free(&props);
        IMFSample_Release(sample);
        return ret;
    }

    hr = IMFTransform_ProcessInput(c->mft, c->in_stream_id, sample, 0);
    IMFSample_Release(sample);
    if (hr == MF_E_NOTACCEPTING) {
        av_packet_free(&props);
        return AVERROR(EAGAIN);
    } else if (FAILED(hr)) {
        av_packet_free(&props);
        av_log(avctx, AV_LOG_ERROR, "the decoder refused a packet: %s\n", ff_hr_str(hr));
        return AVERROR_INVALIDDATA;
    }

    ret = av_fifo_write(c->props, &props, 1);
    if (ret < 0)
        av_packet_free(&props);
    return ret;
}

static int mfdec_sample_to_frame(AVCodecContext *avctx, IMFSample *sample,
                                 AVFrame *frame)
{
    MFDecContext *c = avctx->priv_data;
    IMFMediaBuffer *buffer;
    AVPacket *props = NULL;
    BYTE *data;
    DWORD len = 0;
    HRESULT hr;
    int ret = 0;

    // One output frame for every packet given to the MFT, in order.
    if (av_fifo_read(c->props, &props, 1) < 0)
        props = NULL;

    hr = IMFSample_ConvertToContiguousBuffer(sample, &buffer);
    if (FAILED(hr)) {
        av_packet_free(&props);
        return AVERROR_EXTERNAL;
    }
    hr = IMFMediaBuffer_Lock(buffer, &data, NULL, &len);
    if (FAILED(hr)) {
        IMFMediaBuffer_Release(buffer);
        av_packet_free(&props);
        return AVERROR_EXTERNAL;
    }

    frame->nb_samples = c->bytes_per_frame > 0 ? len / c->bytes_per_frame : 0;
    if (!frame->nb_samples) {
        ret = AVERROR(EAGAIN);
        goto end;
    }
    ret = ff_get_buffer(avctx, frame, 0);
    if (ret < 0)
        goto end;
    memcpy(frame->data[0], data, (size_t)frame->nb_samples * c->bytes_per_frame);

    if (props) {
        ret = ff_decode_frame_props_from_pkt(avctx, frame, props);
        frame->pkt_dts = props->dts;
    } else {
        frame->pts = AV_NOPTS_VALUE;
    }
    if (ret >= 0 && avctx->pkt_timebase.num > 0 && avctx->pkt_timebase.den > 0)
        frame->duration = av_rescale_q(frame->nb_samples,
                                       (AVRational){ 1, avctx->sample_rate },
                                       avctx->pkt_timebase);
    if (ret < 0)
        av_frame_unref(frame);

end:
    IMFMediaBuffer_Unlock(buffer);
    IMFMediaBuffer_Release(buffer);
    av_packet_free(&props);
    return ret;
}

static int mfdec_receive_output(AVCodecContext *avctx, AVFrame *frame)
{
    MFDecContext *c = avctx->priv_data;
    int stream_changes = 0;

    for (;;) {
        MFT_OUTPUT_DATA_BUFFER out = { 0 };
        IMFSample *sample = NULL;
        DWORD status = 0;
        HRESULT hr;
        int ret;

        if (!c->out_stream_provides_samples) {
            sample = ff_create_memory_sample(&c->functions, NULL, c->out_info.cbSize,
                                             c->out_info.cbAlignment);
            if (!sample)
                return AVERROR(ENOMEM);
        }
        out.dwStreamID = c->out_stream_id;
        out.pSample    = sample;

        hr = IMFTransform_ProcessOutput(c->mft, 0, 1, &out, &status);
        if (out.pEvents)
            IMFCollection_Release(out.pEvents);

        if (FAILED(hr)) {
            if (out.pSample)
                IMFSample_Release(out.pSample);
            if (hr == MF_E_TRANSFORM_NEED_MORE_INPUT)
                return AVERROR(EAGAIN);
            if (hr == MF_E_TRANSFORM_STREAM_CHANGE && ++stream_changes <= MAX_STREAM_CHANGES) {
                if ((ret = mfdec_set_output_type(avctx)) < 0)
                    return ret;
                continue;
            }
            av_log(avctx, AV_LOG_ERROR, "failed processing output: %s\n", ff_hr_str(hr));
            mfdec_drop_props(c);
            return AVERROR_INVALIDDATA;
        }
        if (!out.pSample)
            continue;

        ret = mfdec_sample_to_frame(avctx, out.pSample, frame);
        IMFSample_Release(out.pSample);
        if (ret == AVERROR(EAGAIN))
            continue;
        return ret;
    }
}

static int mfdec_receive_frame(AVCodecContext *avctx, AVFrame *frame)
{
    MFDecContext *c = avctx->priv_data;
    HRESULT hr;
    int ret;

    if (!c->stream_started) {
        hr = IMFTransform_ProcessMessage(c->mft, MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0);
        if (!FAILED(hr))
            hr = IMFTransform_ProcessMessage(c->mft, MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0);
        if (FAILED(hr)) {
            av_log(avctx, AV_LOG_ERROR, "could not start the stream: %s\n", ff_hr_str(hr));
            return AVERROR_EXTERNAL;
        }
        c->stream_started = 1;
    }

    for (;;) {
        ret = mfdec_receive_output(avctx, frame);
        if (ret != AVERROR(EAGAIN))
            return ret;
        if (c->draining)
            return AVERROR_EOF;

        av_packet_unref(c->pkt);
        ret = ff_decode_get_packet(avctx, c->pkt);
        if (ret == AVERROR_EOF) {
            hr = IMFTransform_ProcessMessage(c->mft, MFT_MESSAGE_COMMAND_DRAIN, 0);
            if (FAILED(hr))
                av_log(avctx, AV_LOG_WARNING, "failed draining: %s\n", ff_hr_str(hr));
            c->draining = 1;
            continue;
        }
        if (ret < 0)
            return ret;
        if (!c->pkt->size)
            continue;

        ret = mfdec_send_packet(avctx, c->pkt);
        av_packet_unref(c->pkt);
        if (ret == AVERROR(EAGAIN)) {
            // It has just said it has no output: it should take input now.
            av_log(avctx, AV_LOG_ERROR, "the decoder takes neither input nor output\n");
            return AVERROR_EXTERNAL;
        }
        if (ret < 0)
            return ret;
    }
}

static av_cold void mfdec_flush(AVCodecContext *avctx)
{
    MFDecContext *c = avctx->priv_data;

    if (c->mft && c->stream_started)
        IMFTransform_ProcessMessage(c->mft, MFT_MESSAGE_COMMAND_FLUSH, 0);
    c->draining  = 0;
    c->next_time = 0;
    av_packet_unref(c->pkt);
    mfdec_drop_all_props(c);
}

static av_cold int mfdec_close(AVCodecContext *avctx)
{
    MFDecContext *c = avctx->priv_data;

    if (c->props) {
        mfdec_drop_all_props(c);
        av_fifo_freep2(&c->props);
    }
    av_packet_free(&c->pkt);
    if (c->mft)
        IMFTransform_Release(c->mft);
    c->mft = NULL;
    if (c->mf_started)
        c->functions.MFShutdown();
    c->mf_started = 0;
    if (c->com_initialized)
        CoUninitialize();
    c->com_initialized = 0;
#if !HAVE_UWP
    if (c->library)
        dlclose(c->library);
    c->library = NULL;
#endif

    return 0;
}

const FFCodec ff_aac_mf_decoder = {
    .p.name         = "aac_mf",
    CODEC_LONG_NAME("AAC via MediaFoundation"),
    .p.type         = AVMEDIA_TYPE_AUDIO,
    .p.id           = AV_CODEC_ID_AAC,
    .p.capabilities = AV_CODEC_CAP_DELAY | AV_CODEC_CAP_DR1 |
                      AV_CODEC_CAP_CHANNEL_CONF,
    .p.wrapper_name = "mf",
    .priv_data_size = sizeof(MFDecContext),
    .init           = mfdec_init,
    .close          = mfdec_close,
    .flush          = mfdec_flush,
    FF_CODEC_RECEIVE_FRAME_CB(mfdec_receive_frame),
    .caps_internal  = FF_CODEC_CAP_INIT_CLEANUP | FF_CODEC_CAP_SETS_FRAME_PROPS,
};
