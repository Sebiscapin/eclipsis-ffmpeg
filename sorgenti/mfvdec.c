/*
 * H.264 decoder using the Microsoft H.264 Video Decoder MFT (Media Foundation)
 *
 * Written for the FFmpeg build used by Eclipsis
 * (https://github.com/Sebiscapin/eclipsis-ffmpeg), 11 November 2026; not part of
 * the upstream FFmpeg sources.
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
 * H.264 decoding through the decoder that ships with Windows
 * (CLSID_CMSH264DecoderMFT, msmpeg2vdec.dll), so that no H.264 decoder needs to
 * be compiled into the binary.
 *
 * The MFT takes Annex B access units (h264_mp4toannexb converts MP4, MOV and
 * Matroska streams), keeps some of them in flight and returns the pictures in
 * display order, each with the time of its access unit. The properties of every
 * packet (pts, duration, flags, side data) wait in a list and go to the picture
 * with the same time (FF_CODEC_CAP_SETS_FRAME_PROPS); the dts are handed out in
 * decode order, one per picture, so that they always grow.
 *
 * The output is I420, that is yuv420p (NV12 is converted if it is the only
 * choice), cropped to the display aperture; the MFT wants a new output sample
 * for every picture. Range, matrix, primaries, transfer, chroma location,
 * aspect ratio, frame rate and reorder depth come from the sequence parameter
 * set, read here as the native decoder reads them, so that a stream reports the
 * same properties with either decoder (the output type of the MFT is only the
 * fallback). Full range gives yuvj420p, like the native decoder.
 */

#define COBJMACROS
#if !defined(_WIN32_WINNT) || _WIN32_WINNT < 0x0602
#undef _WIN32_WINNT
#define _WIN32_WINNT 0x0602
#endif

#include "config.h"

#include "libavutil/common.h"
#include "libavutil/imgutils.h"
#include "libavutil/intreadwrite.h"
#include "libavutil/mem.h"
#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "libavutil/rational.h"
#include "avcodec.h"
#include "codec_internal.h"
#include "decode.h"
#include "mf_utils.h"
#include "compat/w32dlfcn.h"

#define MFVDEC_GUID(name, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8) \
    static const GUID name = { l, w1, w2, { b1, b2, b3, b4, b5, b6, b7, b8 } }
#define MFVDEC_FOURCC_GUID(name, fourcc) \
    MFVDEC_GUID(name, fourcc, 0x0000, 0x0010, 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71)

// The GUIDs are written out here, so that the file does not depend on which of
// them the headers of the compiler define.
// CLSID_CMSH264DecoderMFT: the Microsoft H.264 decoder, not whatever MFT for
// H.264 has the highest merit on the machine.
MFVDEC_GUID(mfvdec_CLSID_H264Decoder, 0x62ce7e72, 0x4c71, 0x4d20,
            0xb1, 0x5d, 0x45, 0x28, 0x31, 0xa8, 0x7d, 0x9d);
MFVDEC_GUID(mfvdec_AVDecNumWorkerThreads, 0x9561c3e8, 0xea9e, 0x4435,
            0x9b, 0x1e, 0xa9, 0x3e, 0x69, 0x18, 0x94, 0xd8);
MFVDEC_GUID(mfvdec_LOW_LATENCY, 0x9c27891a, 0xed7a, 0x40e1,
            0x88, 0xe8, 0xb2, 0x27, 0x27, 0xa0, 0x24, 0xee);
MFVDEC_GUID(mfvdec_MT_MAJOR_TYPE, 0x48eba18e, 0xf8c9, 0x4687,
            0xbf, 0x11, 0x0a, 0x74, 0xc9, 0xf9, 0x6a, 0x8f);
MFVDEC_GUID(mfvdec_MT_SUBTYPE, 0xf7e34c9a, 0x42e8, 0x4714,
            0xb7, 0x4b, 0xcb, 0x29, 0xd7, 0x2c, 0x35, 0xe5);
MFVDEC_GUID(mfvdec_MT_FRAME_SIZE, 0x1652c33d, 0xd6b2, 0x4012,
            0xb8, 0x34, 0x72, 0x03, 0x08, 0x49, 0xa3, 0x7d);
MFVDEC_GUID(mfvdec_MT_FRAME_RATE, 0xc459a2e8, 0x3d2c, 0x4e44,
            0xb1, 0x32, 0xfe, 0xe5, 0x15, 0x6c, 0x7b, 0xb0);
MFVDEC_GUID(mfvdec_MT_INTERLACE_MODE, 0xe2724bb8, 0xe676, 0x4806,
            0xb4, 0xb2, 0xa8, 0xd6, 0xef, 0xb4, 0x4c, 0xcd);
MFVDEC_GUID(mfvdec_MT_DEFAULT_STRIDE, 0x644b4e48, 0x1e02, 0x4516,
            0xb0, 0xeb, 0xc0, 0x1c, 0xa9, 0xd4, 0x9a, 0xc6);
MFVDEC_GUID(mfvdec_MT_MINIMUM_DISPLAY_APERTURE, 0xd7388766, 0x18fe, 0x48c6,
            0xa1, 0x77, 0xee, 0x89, 0x48, 0x67, 0xc8, 0xc4);
MFVDEC_GUID(mfvdec_MT_GEOMETRIC_APERTURE, 0x66758743, 0x7e5f, 0x400d,
            0x98, 0x0a, 0xaa, 0x85, 0x96, 0xc8, 0x56, 0x96);
MFVDEC_GUID(mfvdec_MT_VIDEO_NOMINAL_RANGE, 0xc21b8ee5, 0xb956, 0x4071,
            0x8d, 0xaf, 0x32, 0x5e, 0xdf, 0x5c, 0xab, 0x11);
MFVDEC_GUID(mfvdec_MT_YUV_MATRIX, 0x3e23d450, 0x2c75, 0x4d25,
            0xa0, 0x0e, 0xb9, 0x16, 0x70, 0xd1, 0x23, 0x27);
MFVDEC_GUID(mfvdec_MT_VIDEO_PRIMARIES, 0xdbfbe4d7, 0x0740, 0x4ee0,
            0x81, 0x92, 0x85, 0x0a, 0xb0, 0xe2, 0x19, 0x35);
MFVDEC_GUID(mfvdec_MT_TRANSFER_FUNCTION, 0x5fb0fce9, 0xbe5c, 0x4935,
            0xa8, 0x11, 0xec, 0x83, 0x8f, 0x8e, 0xed, 0x93);
MFVDEC_GUID(mfvdec_SampleCleanPoint, 0x9cdf01d8, 0xa0f0, 0x43ba,
            0xb0, 0x77, 0xea, 0xa0, 0x6c, 0xbd, 0x72, 0x8a);
MFVDEC_GUID(mfvdec_SampleDiscontinuity, 0x9cdf01d9, 0xa0f0, 0x43ba,
            0xb0, 0x77, 0xea, 0xa0, 0x6c, 0xbd, 0x72, 0x8a);
MFVDEC_GUID(mfvdec_MediaType_Video, 0x73646976, 0x0000, 0x0010,
            0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71);
MFVDEC_FOURCC_GUID(mfvdec_Format_H264, 0x34363248);    // 'H264'
MFVDEC_FOURCC_GUID(mfvdec_Format_I420, 0x30323449);    // 'I420'
MFVDEC_FOURCC_GUID(mfvdec_Format_IYUV, 0x56555949);    // 'IYUV', same layout
MFVDEC_FOURCC_GUID(mfvdec_Format_NV12, 0x3231564e);    // 'NV12'

#ifndef MF_E_TRANSFORM_TYPE_NOT_SET
#define MF_E_TRANSFORM_TYPE_NOT_SET ((HRESULT)0xC00D6D60L)
#endif

#define MAX_STREAM_CHANGES 4    ///< output format changes accepted in a row
#define MAX_PENDING        128  ///< packets given to the MFT, not yet out as pictures
#define MAX_REFUSALS       8    ///< ProcessInput refusals in a row
#define SPS_MAX_SIZE       2048
#define NO_TIME            INT64_MIN
// The first sample time after a start or a flush: room for the earlier times
// of the reordered pictures, so that the times given to the MFT stay positive.
#define FIRST_TIME         INT64_C(1000000000)

/** What the native decoder takes from a sequence parameter set. */
typedef struct MFVDecSPS {
    int valid;                         ///< read from a sequence parameter set
    int range_set;                     ///< video_signal_type_present_flag
    enum AVColorRange range;
    int colour_set;                    ///< colour_description_present_flag
    enum AVColorPrimaries primaries;
    enum AVColorTransferCharacteristic trc;
    enum AVColorSpace space;
    enum AVChromaLocation chroma_loc;
    AVRational sar;                    ///< 0/0 if absent
    AVRational framerate;              ///< 0/0 if absent
    int reorder;                       ///< max_num_reorder_frames, -1 if absent
    int profile, level;
    int chroma_format, bit_depth;
} MFVDecSPS;

typedef struct MFVDecPending {
    int64_t time;                      ///< sample time given to the MFT, NO_TIME if none
    AVPacket *props;
    MFVDecSPS sps;                     ///< parameters in force for this packet
} MFVDecPending;

typedef struct MFVDecContext {
    const AVClass *class;
    int low_latency;                   ///< option: -1 auto (with one thread), 0, 1

    HMODULE library;
    MFFunctions functions;
    int com_initialized;
    int mf_started;
    IMFTransform *mft;
    DWORD in_stream_id, out_stream_id;
    MFT_INPUT_STREAM_INFO in_info;
    MFT_OUTPUT_STREAM_INFO out_info;
    int out_stream_provides_samples;

    int nv12;                          ///< NV12 output, converted to yuv420p
    int out_width, out_height;         ///< size of the decoded pictures
    int pitch;                         ///< bytes per luma line in the output buffers
    int crop_x, crop_y, crop_w, crop_h;
    MFVDecSPS mf_colour;               ///< colour of the output type (fallback)

    int draining;
    int discontinuity;                 ///< the next sample follows a flush
    int have_offset;
    int64_t time_offset;
    int refused;
    int unsupported;                   ///< bit depth or chroma format the MFT cannot read
    MFVDecSPS sps;                     ///< from the last sequence parameter set
    MFVDecPending pending[MAX_PENDING];
    int64_t dts[MAX_PENDING];          ///< dts of the pending packets, in decode order
    int nb_pending;
    AVPacket *pkt;
} MFVDecContext;

/* ---- Sequence parameter set (ITU-T H.264 7.3.2.1.1 and E.1.1) ---- */

typedef struct MFVDecBits {
    const uint8_t *buf;
    int64_t size, pos;                 ///< in bits; pos > size means overread
} MFVDecBits;

static unsigned mfvdec_bits(MFVDecBits *b, int n)
{
    unsigned v = 0;

    while (n-- > 0) {
        int bit = b->pos < b->size ? (b->buf[b->pos >> 3] >> (7 - (b->pos & 7))) & 1 : 0;
        b->pos++;
        v = (v << 1) | bit;
    }
    return v;
}

static unsigned mfvdec_ue(MFVDecBits *b)
{
    int zeros = 0;

    while (!mfvdec_bits(b, 1)) {
        if (++zeros > 31 || b->pos > b->size) {
            b->pos = b->size + 1;
            return 0;
        }
    }
    return (1U << zeros) - 1 + mfvdec_bits(b, zeros);
}

static int mfvdec_se(MFVDecBits *b)
{
    unsigned k = mfvdec_ue(b);

    return k & 1 ? (int)((k >> 1) + 1) : -(int)(k >> 1);
}

static void mfvdec_skip_scaling_list(MFVDecBits *b, int n)
{
    int last = 8, next = 8;

    for (int j = 0; j < n; j++) {
        if (next)
            next = (last + mfvdec_se(b)) & 0xFF;
        if (next)
            last = next;
    }
}

static void mfvdec_skip_hrd(MFVDecBits *b)
{
    unsigned count = mfvdec_ue(b) + 1;

    if (count > 32) {
        b->pos = b->size + 1;
        return;
    }
    mfvdec_bits(b, 8);                       // bit_rate_scale, cpb_size_scale
    for (unsigned i = 0; i < count; i++) {
        mfvdec_ue(b);                        // bit_rate_value_minus1
        mfvdec_ue(b);                        // cpb_size_value_minus1
        mfvdec_bits(b, 1);                   // cbr_flag
    }
    mfvdec_bits(b, 20);                      // four delay and offset lengths
}

// The VUI as h2645_vui.c and h264_ps.c read it, down to max_num_reorder_frames.
static void mfvdec_parse_vui(MFVDecBits *b, MFVDecSPS *s)
{
    static const AVRational sar_table[17] = {
        {   0,  1 }, {   1,  1 }, {  12, 11 }, {  10, 11 }, {  16, 11 }, {  40, 33 },
        {  24, 11 }, {  20, 11 }, {  32, 11 }, {  80, 33 }, {  18, 11 }, {  15, 11 },
        {  64, 33 }, { 160, 99 }, {   4,  3 }, {   3,  2 }, {   2,  1 },
    };
    int nal_hrd, vcl_hrd;

    if (mfvdec_bits(b, 1)) {                 // aspect_ratio_info_present_flag
        unsigned idc = mfvdec_bits(b, 8);
        if (idc == 255) {
            s->sar.num = mfvdec_bits(b, 16);
            s->sar.den = mfvdec_bits(b, 16);
        } else if (idc < FF_ARRAY_ELEMS(sar_table)) {
            s->sar = sar_table[idc];
        }
    }
    if (mfvdec_bits(b, 1))                   // overscan_info_present_flag
        mfvdec_bits(b, 1);
    if (mfvdec_bits(b, 1)) {                 // video_signal_type_present_flag
        mfvdec_bits(b, 3);                   // video_format
        s->range_set = 1;
        s->range     = mfvdec_bits(b, 1) ? AVCOL_RANGE_JPEG : AVCOL_RANGE_MPEG;
        if (mfvdec_bits(b, 1)) {             // colour_description_present_flag
            s->colour_set = 1;
            s->primaries  = mfvdec_bits(b, 8);
            s->trc        = mfvdec_bits(b, 8);
            s->space      = mfvdec_bits(b, 8);
            if (!av_color_primaries_name(s->primaries))
                s->primaries = AVCOL_PRI_UNSPECIFIED;
            if (!av_color_transfer_name(s->trc))
                s->trc = AVCOL_TRC_UNSPECIFIED;
            if (!av_color_space_name(s->space))
                s->space = AVCOL_SPC_UNSPECIFIED;
        }
    }
    s->chroma_loc = AVCHROMA_LOC_LEFT;
    if (mfvdec_bits(b, 1)) {                 // chroma_loc_info_present_flag
        unsigned top = mfvdec_ue(b);
        mfvdec_ue(b);
        s->chroma_loc = top <= 5 ? top + 1 : AVCHROMA_LOC_UNSPECIFIED;
    }
    if (b->pos > b->size)
        return;

    if (mfvdec_bits(b, 1)) {                 // timing_info_present_flag
        unsigned units = mfvdec_bits(b, 32), scale = mfvdec_bits(b, 32);
        mfvdec_bits(b, 1);                   // fixed_frame_rate_flag
        if (units && scale && b->pos <= b->size)
            av_reduce(&s->framerate.den, &s->framerate.num, 2 * (int64_t)units,
                      scale, 1 << 30);
    }
    nal_hrd = mfvdec_bits(b, 1);
    if (nal_hrd)
        mfvdec_skip_hrd(b);
    vcl_hrd = mfvdec_bits(b, 1);
    if (vcl_hrd)
        mfvdec_skip_hrd(b);
    if (nal_hrd || vcl_hrd)
        mfvdec_bits(b, 1);                   // low_delay_hrd_flag
    mfvdec_bits(b, 1);                       // pic_struct_present_flag
    if (mfvdec_bits(b, 1)) {                 // bitstream_restriction_flag
        unsigned reorder;
        mfvdec_bits(b, 1);
        for (int i = 0; i < 4; i++)
            mfvdec_ue(b);
        reorder = mfvdec_ue(b);              // max_num_reorder_frames
        mfvdec_ue(b);                        // max_dec_frame_buffering
        if (b->pos <= b->size && reorder <= 16)
            s->reorder = reorder;
    }
}

static int mfvdec_parse_sps(const uint8_t *rbsp, int size, MFVDecSPS *s)
{
    MFVDecBits b = { rbsp, 8 * (int64_t)size, 0 };
    unsigned profile, constraints, level, chroma = 1, depth = 8, poc_type;

    memset(s, 0, sizeof(*s));
    s->reorder = -1;

    profile     = mfvdec_bits(&b, 8);
    constraints = mfvdec_bits(&b, 8);
    level       = mfvdec_bits(&b, 8);
    if (mfvdec_ue(&b) > 31)                  // seq_parameter_set_id
        return AVERROR_INVALIDDATA;
    if (profile == 100 || profile == 110 || profile == 122 || profile == 244 ||
        profile ==  44 || profile ==  83 || profile ==  86 || profile == 118 ||
        profile == 128 || profile == 138 || profile == 139 || profile == 134 ||
        profile == 135) {
        chroma = mfvdec_ue(&b);
        if (chroma > 3)
            return AVERROR_INVALIDDATA;
        if (chroma == 3)
            mfvdec_bits(&b, 1);              // separate_colour_plane_flag
        depth = mfvdec_ue(&b) + 8;           // bit_depth_luma_minus8
        mfvdec_ue(&b);                       // bit_depth_chroma_minus8
        mfvdec_bits(&b, 1);                  // qpprime_y_zero_transform_bypass_flag
        if (mfvdec_bits(&b, 1))              // seq_scaling_matrix_present_flag
            for (int i = 0; i < (chroma != 3 ? 8 : 12); i++)
                if (mfvdec_bits(&b, 1))
                    mfvdec_skip_scaling_list(&b, i < 6 ? 16 : 64);
    }
    mfvdec_ue(&b);                           // log2_max_frame_num_minus4
    poc_type = mfvdec_ue(&b);
    if (poc_type == 0) {
        mfvdec_ue(&b);
    } else if (poc_type == 1) {
        unsigned cycle;
        mfvdec_bits(&b, 1);
        mfvdec_se(&b);
        mfvdec_se(&b);
        cycle = mfvdec_ue(&b);
        if (cycle > 255)
            return AVERROR_INVALIDDATA;
        for (unsigned i = 0; i < cycle; i++)
            mfvdec_se(&b);
    } else if (poc_type != 2) {
        return AVERROR_INVALIDDATA;
    }
    mfvdec_ue(&b);                           // max_num_ref_frames
    mfvdec_bits(&b, 1);                      // gaps_in_frame_num_value_allowed_flag
    mfvdec_ue(&b);                           // pic_width_in_mbs_minus1
    mfvdec_ue(&b);                           // pic_height_in_map_units_minus1
    if (!mfvdec_bits(&b, 1))                 // frame_mbs_only_flag
        mfvdec_bits(&b, 1);
    mfvdec_bits(&b, 1);                      // direct_8x8_inference_flag
    if (mfvdec_bits(&b, 1))                  // frame_cropping_flag
        for (int i = 0; i < 4; i++)
            mfvdec_ue(&b);
    if (b.pos > b.size)
        return AVERROR_INVALIDDATA;

    // ff_h264_get_profile(): constraint_set1 for Baseline, constraint_set3 for the intra profiles
    s->profile = profile;
    if (profile == 66 && (constraints & 0x40))
        s->profile |= AV_PROFILE_H264_CONSTRAINED;
    if ((profile == 110 || profile == 122 || profile == 244) && (constraints & 0x10))
        s->profile |= AV_PROFILE_H264_INTRA;
    s->level         = level;
    s->chroma_format = chroma;
    s->bit_depth     = depth;
    s->chroma_loc    = AVCHROMA_LOC_UNSPECIFIED;
    if (mfvdec_bits(&b, 1))                  // vui_parameters_present_flag
        mfvdec_parse_vui(&b, s);
    s->valid = 1;
    return 0;
}

// Reads a sequence parameter set NAL unit (header byte first), up to its end or
// to the next start code.
static void mfvdec_read_sps(AVCodecContext *avctx, const uint8_t *nal, const uint8_t *end)
{
    MFVDecContext *c = avctx->priv_data;
    uint8_t rbsp[SPS_MAX_SIZE];
    int n = 0, zeros = 0;
    MFVDecSPS s;

    for (const uint8_t *q = nal + 1; q < end && n < (int)sizeof(rbsp); q++) {
        if (zeros >= 2 && *q < 3)            // next start code
            break;
        if (zeros >= 2 && *q == 3) {         // emulation prevention byte
            zeros = 0;
            continue;
        }
        rbsp[n++] = *q;
        zeros = *q ? 0 : zeros + 1;
    }
    if (mfvdec_parse_sps(rbsp, n, &s) < 0) {
        av_log(avctx, AV_LOG_DEBUG, "could not read a sequence parameter set\n");
        return;
    }
    if (s.chroma_format != 1 || s.bit_depth != 8) {
        if (!c->unsupported)
            av_log(avctx, AV_LOG_ERROR, "H.264 with %d bit and chroma format %d: the "
                   "Windows decoder reads only 8 bit 4:2:0\n", s.bit_depth, s.chroma_format);
        c->unsupported = 1;
        return;
    }
    c->unsupported = 0;
    c->sps = s;
}

// The first sequence parameter set of some Annex B data; it comes before the
// slices, so the search stops at the first one.
static void mfvdec_find_sps(AVCodecContext *avctx, const uint8_t *buf, int size)
{
    const uint8_t *end = buf + size;

    for (const uint8_t *p = buf; p + 3 < end; p++) {
        int type;
        if (p[0] || p[1] || p[2] != 1)
            continue;
        p   += 3;
        type = p[0] & 0x1F;
        if (type == 7) {
            mfvdec_read_sps(avctx, p, end);
            return;
        }
        if (type >= 1 && type <= 5)
            return;
    }
}

static void mfvdec_sps_from_extradata(AVCodecContext *avctx)
{
    const uint8_t *x = avctx->extradata;
    int size = avctx->extradata_size;

    if (!x || size < 4)
        return;
    if (x[0] == 1) {                         // avcC: the first SPS
        int len;
        if (size < 8 || !(x[5] & 0x1F))
            return;
        len = AV_RB16(x + 6);
        if (len > 0 && 8 + len <= size && (x[8] & 0x1F) == 7)
            mfvdec_read_sps(avctx, x + 8, x + 8 + len);
    } else {
        mfvdec_find_sps(avctx, x, size);
    }
}

/* ---- Properties of the pictures ---- */

static void mfvdec_read_type_colour(IMFMediaType *type, MFVDecSPS *s)
{
    UINT32 v;

    memset(s, 0, sizeof(*s));
    s->reorder = -1;
    if (SUCCEEDED(IMFMediaType_GetUINT32(type, &mfvdec_MT_VIDEO_NOMINAL_RANGE, &v)) &&
        (v == 1 || v == 2)) {
        s->range_set = 1;
        s->range     = v == 1 ? AVCOL_RANGE_JPEG : AVCOL_RANGE_MPEG;
    }
    s->primaries = AVCOL_PRI_UNSPECIFIED;
    s->trc       = AVCOL_TRC_UNSPECIFIED;
    s->space     = AVCOL_SPC_UNSPECIFIED;
    if (SUCCEEDED(IMFMediaType_GetUINT32(type, &mfvdec_MT_YUV_MATRIX, &v))) {
        static const enum AVColorSpace m[] = {
            AVCOL_SPC_UNSPECIFIED, AVCOL_SPC_BT709, AVCOL_SPC_SMPTE170M,
            AVCOL_SPC_SMPTE240M, AVCOL_SPC_BT2020_NCL, AVCOL_SPC_BT2020_NCL,
        };
        if (v < FF_ARRAY_ELEMS(m))
            s->space = m[v];
    }
    if (SUCCEEDED(IMFMediaType_GetUINT32(type, &mfvdec_MT_VIDEO_PRIMARIES, &v))) {
        static const enum AVColorPrimaries m[] = {
            AVCOL_PRI_UNSPECIFIED, AVCOL_PRI_UNSPECIFIED, AVCOL_PRI_BT709,
            AVCOL_PRI_BT470M, AVCOL_PRI_BT470BG, AVCOL_PRI_SMPTE170M,
            AVCOL_PRI_SMPTE240M, AVCOL_PRI_JEDEC_P22, AVCOL_PRI_SMPTE170M,
            AVCOL_PRI_BT2020, AVCOL_PRI_SMPTE428, AVCOL_PRI_SMPTE431,
        };
        if (v < FF_ARRAY_ELEMS(m))
            s->primaries = m[v];
    }
    if (SUCCEEDED(IMFMediaType_GetUINT32(type, &mfvdec_MT_TRANSFER_FUNCTION, &v))) {
        static const enum AVColorTransferCharacteristic m[] = {
            AVCOL_TRC_UNSPECIFIED, AVCOL_TRC_LINEAR, AVCOL_TRC_UNSPECIFIED,
            AVCOL_TRC_UNSPECIFIED, AVCOL_TRC_GAMMA22, AVCOL_TRC_BT709,
            AVCOL_TRC_SMPTE240M, AVCOL_TRC_IEC61966_2_1, AVCOL_TRC_GAMMA28,
            AVCOL_TRC_LOG, AVCOL_TRC_LOG_SQRT, AVCOL_TRC_UNSPECIFIED,
            AVCOL_TRC_UNSPECIFIED, AVCOL_TRC_BT2020_10, AVCOL_TRC_UNSPECIFIED,
            AVCOL_TRC_SMPTE2084, AVCOL_TRC_ARIB_STD_B67,
        };
        if (v < FF_ARRAY_ELEMS(m))
            s->trc = m[v];
    }
    s->colour_set = s->primaries != AVCOL_PRI_UNSPECIFIED ||
                    s->trc != AVCOL_TRC_UNSPECIFIED || s->space != AVCOL_SPC_UNSPECIFIED;
}

// The parameters in force for a picture, set on the context as the native
// decoder sets them; the pictures take them from there (ff_get_buffer).
static void mfvdec_apply_sps(AVCodecContext *avctx, const MFVDecSPS *s)
{
    MFVDecContext *c = avctx->priv_data;

    if (s->valid) {
        if (s->range_set)
            avctx->color_range = s->range;
        if (s->colour_set) {
            avctx->color_primaries = s->primaries;
            avctx->color_trc       = s->trc;
            avctx->colorspace      = s->space;
        }
        avctx->chroma_sample_location = s->chroma_loc;
        if (s->sar.num > 0 && s->sar.den > 0)
            avctx->sample_aspect_ratio = s->sar;
        if (s->framerate.num > 0 && s->framerate.den > 0)
            avctx->framerate = s->framerate;
        if (s->reorder > avctx->has_b_frames)
            avctx->has_b_frames = s->reorder;
        avctx->profile = s->profile;
        avctx->level   = s->level;
    } else {
        // No sequence parameter set read: what the output type says, if anything.
        const MFVDecSPS *t = &c->mf_colour;
        if (t->range_set)
            avctx->color_range = t->range;
        if (t->colour_set) {
            if (t->primaries != AVCOL_PRI_UNSPECIFIED)
                avctx->color_primaries = t->primaries;
            if (t->trc != AVCOL_TRC_UNSPECIFIED)
                avctx->color_trc = t->trc;
            if (t->space != AVCOL_SPC_UNSPECIFIED)
                avctx->colorspace = t->space;
        }
    }
    avctx->pix_fmt = avctx->color_range == AVCOL_RANGE_JPEG ? AV_PIX_FMT_YUVJ420P
                                                             : AV_PIX_FMT_YUV420P;
}

/* ---- Packets waiting for their pictures ---- */

// Removes pending entry i and the oldest dts.
static void mfvdec_remove_pending(MFVDecContext *c, int i, int free_props)
{
    if (free_props)
        av_packet_free(&c->pending[i].props);
    memmove(&c->pending[i], &c->pending[i + 1], (c->nb_pending - i - 1) * sizeof(*c->pending));
    memmove(&c->dts[0], &c->dts[1], (c->nb_pending - 1) * sizeof(*c->dts));
    c->nb_pending--;
}

static void mfvdec_drop_all_pending(MFVDecContext *c)
{
    while (c->nb_pending > 0)
        mfvdec_remove_pending(c, c->nb_pending - 1, 1);
}

// The packet of the picture just out: the one with the same time, otherwise the
// one with the smallest time (packets without a time count as earliest, in
// order). Packets with an earlier time than the picture will never come out (the
// MFT has dropped them): they go. Returns 0 if nothing is pending.
static int mfvdec_take_pending(AVCodecContext *avctx, int64_t time, int has_time,
                               MFVDecPending *out, int64_t *dts)
{
    MFVDecContext *c = avctx->priv_data;
    int best = -1, exact = 0, depth = 0;

    if (!c->nb_pending)
        return 0;
    if (has_time)
        for (int i = 0; i < c->nb_pending && best < 0; i++)
            if (c->pending[i].time == time)
                best = i, exact = 1;
    if (best < 0) {
        best = 0;
        for (int i = 1; i < c->nb_pending; i++)
            if (c->pending[i].time < c->pending[best].time)
                best = i;
    }
    // Pictures decoded before this one and shown after it: the reorder depth.
    for (int i = 0; i < best; i++)
        if (c->pending[i].time != NO_TIME && c->pending[i].time > c->pending[best].time)
            depth++;
    if (depth > avctx->has_b_frames && depth <= 16)
        avctx->has_b_frames = depth;

    *out = c->pending[best];
    *dts = c->dts[0];
    mfvdec_remove_pending(c, best, 0);
    if (exact)
        for (int i = 0; i < c->nb_pending; )
            if (c->pending[i].time != NO_TIME && c->pending[i].time < time)
                mfvdec_remove_pending(c, i, 1);
            else
                i++;
    return 1;
}

/* ---- Media Foundation ---- */

static int mfvdec_load_library(AVCodecContext *avctx)
{
    MFVDecContext *c = avctx->priv_data;

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

static int mfvdec_set_input_type(AVCodecContext *avctx)
{
    MFVDecContext *c = avctx->priv_data;
    IMFMediaType *type;
    HRESULT hr;

    hr = c->functions.MFCreateMediaType(&type);
    if (FAILED(hr))
        return AVERROR(ENOMEM);
    IMFMediaType_SetGUID(type, &mfvdec_MT_MAJOR_TYPE, &mfvdec_MediaType_Video);
    IMFMediaType_SetGUID(type, &mfvdec_MT_SUBTYPE, &mfvdec_Format_H264);
    if (avctx->width > 0 && avctx->height > 0)
        IMFMediaType_SetUINT64(type, &mfvdec_MT_FRAME_SIZE,
                               ((UINT64)avctx->width << 32) | (UINT32)avctx->height);
    if (avctx->framerate.num > 0 && avctx->framerate.den > 0)
        IMFMediaType_SetUINT64(type, &mfvdec_MT_FRAME_RATE,
                               ((UINT64)avctx->framerate.num << 32) | (UINT32)avctx->framerate.den);
    IMFMediaType_SetUINT32(type, &mfvdec_MT_INTERLACE_MODE, 2);   // MFVideoInterlace_Progressive

    hr = IMFTransform_SetInputType(c->mft, c->in_stream_id, type, 0);
    IMFMediaType_Release(type);
    if (FAILED(hr)) {
        av_log(avctx, AV_LOG_ERROR, "the Windows H.264 decoder refused the input "
               "type: %s\n", ff_hr_str(hr));
        return AVERROR_EXTERNAL;
    }
    return 0;
}

// Size, pitch, crop and colour of the output type in force.
static int mfvdec_read_output_type(AVCodecContext *avctx)
{
    MFVDecContext *c = avctx->priv_data;
    IMFMediaType *type;
    UINT64 size = 0;
    UINT32 stride = 0, area_size = 0, w, h;
    UINT8 area[16];       // MFVideoArea: OffsetX, OffsetY (MFOffset), Area (SIZE)
    HRESULT hr;
    int ret;

    hr = IMFTransform_GetOutputCurrentType(c->mft, c->out_stream_id, &type);
    if (FAILED(hr)) {
        av_log(avctx, AV_LOG_ERROR, "no output type: %s\n", ff_hr_str(hr));
        return AVERROR_EXTERNAL;
    }
    hr = IMFMediaType_GetUINT64(type, &mfvdec_MT_FRAME_SIZE, &size);
    w  = (UINT32)(size >> 32);
    h  = (UINT32)size;
    if (FAILED(hr) || !w || !h || w > 16384 || h > 16384) {
        // Before the first picture the size can still be unknown: the stream
        // change that comes with the first picture brings it.
        IMFMediaType_Release(type);
        c->out_width = c->out_height = 0;
        return 0;
    }
    c->out_width  = w;
    c->out_height = h;
    c->pitch      = w;
    if (SUCCEEDED(IMFMediaType_GetUINT32(type, &mfvdec_MT_DEFAULT_STRIDE, &stride)) &&
        (INT32)stride >= (INT32)w && (INT32)stride <= 32768)
        c->pitch = (INT32)stride;
    c->crop_x = c->crop_y = 0;
    c->crop_w = w;
    c->crop_h = h;
    if ((SUCCEEDED(IMFMediaType_GetBlob(type, &mfvdec_MT_MINIMUM_DISPLAY_APERTURE, area,
                                        sizeof(area), &area_size)) ||
         SUCCEEDED(IMFMediaType_GetBlob(type, &mfvdec_MT_GEOMETRIC_APERTURE, area,
                                        sizeof(area), &area_size))) &&
        area_size >= sizeof(area)) {
        int x = (int16_t)AV_RL16(area + 2), y = (int16_t)AV_RL16(area + 6);
        int cw = (int32_t)AV_RL32(area + 8), ch = (int32_t)AV_RL32(area + 12);
        if (x >= 0 && y >= 0 && cw > 0 && ch > 0 && x + cw <= (int)w && y + ch <= (int)h) {
            c->crop_x = x;
            c->crop_y = y;
            c->crop_w = cw;
            c->crop_h = ch;
        }
    }
    mfvdec_read_type_colour(type, &c->mf_colour);
    IMFMediaType_Release(type);

    hr = IMFTransform_GetOutputStreamInfo(c->mft, c->out_stream_id, &c->out_info);
    if (FAILED(hr))
        return AVERROR_EXTERNAL;
    c->out_stream_provides_samples =
        (c->out_info.dwFlags & MFT_OUTPUT_STREAM_PROVIDES_SAMPLES) ||
        (c->out_info.dwFlags & MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES);
    if (c->out_info.cbSize < (DWORD)c->pitch * h * 3 / 2)
        c->out_info.cbSize = (DWORD)c->pitch * h * 3 / 2;

    ret = av_image_check_size2(c->crop_w, c->crop_h, avctx->max_pixels,
                               AV_PIX_FMT_NONE, 0, avctx);
    if (ret < 0)
        return ret;
    // coded_width and coded_height as the native decoder reports them (ffprobe shows
    // 1920x1080, not the 1088 lines decoded)
    avctx->coded_width  = c->crop_w;
    avctx->coded_height = c->crop_h;
    avctx->width        = c->crop_w;
    avctx->height       = c->crop_h;
    av_log(avctx, AV_LOG_VERBOSE, "output: %s %ux%u (pitch %d), picture %dx%d at %d,%d\n",
           c->nv12 ? "NV12" : "I420", (unsigned)w, (unsigned)h, c->pitch,
           c->crop_w, c->crop_h, c->crop_x, c->crop_y);
    return 0;
}

// I420 first (the same as yuv420p), then IYUV (the same layout), then NV12.
static int mfvdec_set_output_type(AVCodecContext *avctx)
{
    MFVDecContext *c = avctx->priv_data;
    IMFMediaType *chosen = NULL;
    int rank = INT_MAX;
    HRESULT hr;

    for (DWORD n = 0; ; n++) {
        IMFMediaType *type;
        GUID sub;
        int r = INT_MAX;

        hr = IMFTransform_GetOutputAvailableType(c->mft, c->out_stream_id, n, &type);
        if (FAILED(hr))
            break;
        if (SUCCEEDED(IMFMediaType_GetGUID(type, &mfvdec_MT_SUBTYPE, &sub))) {
            if (!memcmp(&sub, &mfvdec_Format_I420, sizeof(sub)))
                r = 0;
            else if (!memcmp(&sub, &mfvdec_Format_IYUV, sizeof(sub)))
                r = 1;
            else if (!memcmp(&sub, &mfvdec_Format_NV12, sizeof(sub)))
                r = 2;
        }
        if (r < rank) {
            if (chosen)
                IMFMediaType_Release(chosen);
            chosen = type;
            rank   = r;
        } else {
            IMFMediaType_Release(type);
        }
    }
    if (!chosen) {
        av_log(avctx, AV_LOG_ERROR, "the decoder offers neither I420 nor NV12 output\n");
        return AVERROR_EXTERNAL;
    }
    hr = IMFTransform_SetOutputType(c->mft, c->out_stream_id, chosen, 0);
    IMFMediaType_Release(chosen);
    if (FAILED(hr)) {
        av_log(avctx, AV_LOG_ERROR, "could not set the output type: %s\n", ff_hr_str(hr));
        return AVERROR_EXTERNAL;
    }
    c->nv12 = rank == 2;
    return mfvdec_read_output_type(avctx);
}

static av_cold int mfvdec_init(AVCodecContext *avctx)
{
    MFVDecContext *c = avctx->priv_data;
    IMFAttributes *attrs = NULL;
    int low_latency;
    HRESULT hr;
    int ret;

    c->pkt = av_packet_alloc();
    if (!c->pkt)
        return AVERROR(ENOMEM);
    c->sps.reorder = -1;

    // The stream properties before the first picture, as the native decoder has them.
    mfvdec_sps_from_extradata(avctx);
    if (c->unsupported)
        return AVERROR_PATCHWELCOME;
    mfvdec_apply_sps(avctx, &c->sps);

    if ((ret = mfvdec_load_library(avctx)) < 0)
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

    hr = CoCreateInstance(&mfvdec_CLSID_H264Decoder, NULL, CLSCTX_INPROC_SERVER,
                          &IID_IMFTransform, (void **)&c->mft);
    if (FAILED(hr)) {
        av_log(avctx, AV_LOG_ERROR, "the Windows H.264 decoder is not available "
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

    // Worker threads (0: the MFT decides, one per core) and low latency: pictures
    // out after a few access units instead of a few dozen, about 8% slower with
    // one thread. One thread is what libavformat uses to probe a stream.
    low_latency = c->low_latency >= 0 ? c->low_latency :
                  avctx->thread_count == 1 || (avctx->flags & AV_CODEC_FLAG_LOW_DELAY);
    hr = IMFTransform_GetAttributes(c->mft, &attrs);
    if (SUCCEEDED(hr)) {
        if (avctx->thread_count > 0) {
            hr = IMFAttributes_SetUINT32(attrs, &mfvdec_AVDecNumWorkerThreads,
                                         avctx->thread_count);
            if (FAILED(hr))
                av_log(avctx, AV_LOG_WARNING, "could not set %d threads: %s\n",
                       avctx->thread_count, ff_hr_str(hr));
        }
        if (low_latency) {
            hr = IMFAttributes_SetUINT32(attrs, &mfvdec_LOW_LATENCY, 1);
            if (FAILED(hr))
                av_log(avctx, AV_LOG_WARNING, "could not set low latency: %s\n",
                       ff_hr_str(hr));
        }
        IMFAttributes_Release(attrs);
    }

    if ((ret = mfvdec_set_input_type(avctx)) < 0)
        return ret;
    hr = IMFTransform_GetInputStreamInfo(c->mft, c->in_stream_id, &c->in_info);
    if (FAILED(hr))
        return AVERROR_EXTERNAL;
    if ((ret = mfvdec_set_output_type(avctx)) < 0)
        return ret;

    hr = IMFTransform_ProcessMessage(c->mft, MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0);
    if (!FAILED(hr))
        hr = IMFTransform_ProcessMessage(c->mft, MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0);
    if (FAILED(hr)) {
        av_log(avctx, AV_LOG_ERROR, "could not start the stream: %s\n", ff_hr_str(hr));
        return AVERROR_EXTERNAL;
    }
    av_log(avctx, AV_LOG_VERBOSE, "Windows H.264 decoder: %d threads%s\n",
           avctx->thread_count, low_latency ? ", low latency" : "");
    return 0;
}

static int mfvdec_send_packet(AVCodecContext *avctx, const AVPacket *pkt)
{
    MFVDecContext *c = avctx->priv_data;
    int64_t time = NO_TIME;
    MFVDecPending *p;
    IMFSample *sample;
    AVPacket *props;
    HRESULT hr;
    int ret;

    // A sequence parameter set in the packet: the parameters of the pictures from here on.
    mfvdec_find_sps(avctx, pkt->data, pkt->size);
    if (c->unsupported)
        return AVERROR_PATCHWELCOME;

    props = av_packet_alloc();
    if (!props)
        return AVERROR(ENOMEM);
    ret = av_packet_copy_props(props, pkt);
    if (ret < 0) {
        av_packet_free(&props);
        return ret;
    }
    sample = ff_create_memory_sample(&c->functions, pkt->data, pkt->size,
                                     c->in_info.cbAlignment);
    if (!sample) {
        av_packet_free(&props);
        return AVERROR(ENOMEM);
    }

    // The MFT gives each picture the time of its access unit: the pts in 100 ns
    // units, moved so that they stay positive, find its packet again.
    if (pkt->pts != AV_NOPTS_VALUE) {
        int tb = avctx->pkt_timebase.num > 0 && avctx->pkt_timebase.den > 0;
        time = tb ? av_rescale_q(pkt->pts, avctx->pkt_timebase, (AVRational){ 1, 10000000 })
                  : pkt->pts;
        if (!c->have_offset) {
            c->time_offset = FIRST_TIME - time;
            c->have_offset = 1;
        }
        time += c->time_offset;
        IMFSample_SetSampleTime(sample, time);
        if (tb && pkt->duration > 0)
            IMFSample_SetSampleDuration(sample, av_rescale_q(pkt->duration, avctx->pkt_timebase,
                                                             (AVRational){ 1, 10000000 }));
    }
    if (pkt->flags & AV_PKT_FLAG_KEY)
        IMFSample_SetUINT32(sample, &mfvdec_SampleCleanPoint, 1);
    if (c->discontinuity)
        IMFSample_SetUINT32(sample, &mfvdec_SampleDiscontinuity, 1);

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
    c->discontinuity = 0;

    // The oldest have waited too long: their pictures will not come.
    if (c->nb_pending == MAX_PENDING)
        mfvdec_remove_pending(c, 0, 1);
    p = &c->pending[c->nb_pending];
    p->time  = time;
    p->props = props;
    p->sps   = c->sps;
    c->dts[c->nb_pending] = pkt->dts;
    c->nb_pending++;
    return 0;
}

static void mfvdec_copy_picture(MFVDecContext *c, AVFrame *frame, const uint8_t *data)
{
    const int pitch = c->pitch, cw = (c->crop_w + 1) >> 1, ch = (c->crop_h + 1) >> 1;
    const uint8_t *chroma = data + (size_t)pitch * c->out_height;

    av_image_copy_plane(frame->data[0], frame->linesize[0],
                        data + (size_t)c->crop_y * pitch + c->crop_x, pitch,
                        c->crop_w, c->crop_h);
    if (!c->nv12) {
        const int cpitch = pitch >> 1;
        const size_t offset = (size_t)(c->crop_y >> 1) * cpitch + (c->crop_x >> 1);
        const uint8_t *u = chroma + offset;
        const uint8_t *v = chroma + (size_t)cpitch * ((c->out_height + 1) >> 1) + offset;
        av_image_copy_plane(frame->data[1], frame->linesize[1], u, cpitch, cw, ch);
        av_image_copy_plane(frame->data[2], frame->linesize[2], v, cpitch, cw, ch);
    } else {
        const uint8_t *uv = chroma + (size_t)(c->crop_y >> 1) * pitch + (c->crop_x & ~1);
        for (int y = 0; y < ch; y++) {
            uint8_t *u = frame->data[1] + (size_t)y * frame->linesize[1];
            uint8_t *v = frame->data[2] + (size_t)y * frame->linesize[2];
            for (int x = 0; x < cw; x++) {
                u[x] = uv[2 * x];
                v[x] = uv[2 * x + 1];
            }
            uv += pitch;
        }
    }
}

static int mfvdec_sample_to_frame(AVCodecContext *avctx, IMFSample *sample, AVFrame *frame)
{
    MFVDecContext *c = avctx->priv_data;
    MFVDecPending p = { .time = NO_TIME, .props = NULL };
    int64_t dts = AV_NOPTS_VALUE;
    IMFMediaBuffer *buffer;
    LONGLONG time = 0;
    size_t need;
    BYTE *data;
    DWORD len = 0;
    HRESULT hr;
    int has_time, has_props, ret = 0;

    has_time  = SUCCEEDED(IMFSample_GetSampleTime(sample, &time));
    has_props = mfvdec_take_pending(avctx, time, has_time, &p, &dts);
    if (has_props && (p.props->flags & AV_PKT_FLAG_DISCARD)) {
        av_packet_free(&p.props);
        return AVERROR(EAGAIN);
    }
    if (!c->out_width) {
        av_packet_free(&p.props);
        av_log(avctx, AV_LOG_ERROR, "a picture before its size\n");
        return AVERROR_EXTERNAL;
    }
    mfvdec_apply_sps(avctx, has_props ? &p.sps : &c->sps);

    hr = IMFSample_ConvertToContiguousBuffer(sample, &buffer);
    if (FAILED(hr)) {
        av_packet_free(&p.props);
        return AVERROR_EXTERNAL;
    }
    hr = IMFMediaBuffer_Lock(buffer, &data, NULL, &len);
    if (FAILED(hr)) {
        IMFMediaBuffer_Release(buffer);
        av_packet_free(&p.props);
        return AVERROR_EXTERNAL;
    }
    need = (size_t)c->pitch * c->out_height + (c->nv12 ? (size_t)c->pitch :
           2 * (size_t)(c->pitch >> 1)) * ((c->out_height + 1) >> 1);
    if (len < need) {
        av_log(avctx, AV_LOG_ERROR, "picture of %u bytes, %u expected\n", (unsigned)len,
               (unsigned)need);
        ret = AVERROR_EXTERNAL;
        goto end;
    }

    ret = ff_get_buffer(avctx, frame, 0);
    if (ret < 0)
        goto end;
    mfvdec_copy_picture(c, frame, data);

    if (has_props) {
        ret = ff_decode_frame_props_from_pkt(avctx, frame, p.props);
        if (p.props->flags & AV_PKT_FLAG_KEY) {
            frame->flags    |= AV_FRAME_FLAG_KEY;
            frame->pict_type = AV_PICTURE_TYPE_I;
        }
    } else {
        frame->pts = AV_NOPTS_VALUE;
    }
    frame->pkt_dts = dts;
    if (ret < 0)
        av_frame_unref(frame);

end:
    IMFMediaBuffer_Unlock(buffer);
    IMFMediaBuffer_Release(buffer);
    av_packet_free(&p.props);
    return ret;
}

static int mfvdec_receive_output(AVCodecContext *avctx, AVFrame *frame)
{
    MFVDecContext *c = avctx->priv_data;
    int stream_changes = 0, empty = 0;

    for (;;) {
        MFT_OUTPUT_DATA_BUFFER out = { 0 };
        IMFSample *sample = NULL;
        DWORD status = 0;
        HRESULT hr;
        int ret;

        if (!c->out_stream_provides_samples) {
            // A new sample for every picture: the MFT refuses one it has already filled.
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
            if ((hr == MF_E_TRANSFORM_STREAM_CHANGE || hr == MF_E_TRANSFORM_TYPE_NOT_SET) &&
                ++stream_changes <= MAX_STREAM_CHANGES) {
                if ((ret = mfvdec_set_output_type(avctx)) < 0)
                    return ret;
                continue;
            }
            // Start again from the next packets (the pictures in flight are lost),
            // or every call would fail the same way.
            av_log(avctx, AV_LOG_ERROR, "failed processing output: %s\n", ff_hr_str(hr));
            IMFTransform_ProcessMessage(c->mft, MFT_MESSAGE_COMMAND_FLUSH, 0);
            mfvdec_drop_all_pending(c);
            c->discontinuity = 1;
            return AVERROR_INVALIDDATA;
        }
        if (!out.pSample) {
            if (++empty > MAX_STREAM_CHANGES)
                return AVERROR_EXTERNAL;
            continue;
        }

        ret = mfvdec_sample_to_frame(avctx, out.pSample, frame);
        IMFSample_Release(out.pSample);
        if (ret == AVERROR(EAGAIN))
            continue;
        return ret;
    }
}

static int mfvdec_receive_frame(AVCodecContext *avctx, AVFrame *frame)
{
    MFVDecContext *c = avctx->priv_data;
    HRESULT hr;
    int ret;

    for (;;) {
        ret = mfvdec_receive_output(avctx, frame);
        if (ret != AVERROR(EAGAIN))
            return ret;
        if (c->draining)
            return AVERROR_EOF;

        // c->pkt keeps a packet the MFT has not taken yet.
        if (!c->pkt->size) {
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
            // -skip_frame: the pictures not wanted never reach the MFT.
            if (!c->pkt->size || avctx->skip_frame >= AVDISCARD_ALL ||
                (avctx->skip_frame >= AVDISCARD_NONKEY && !(c->pkt->flags & AV_PKT_FLAG_KEY))) {
                av_packet_unref(c->pkt);
                continue;
            }
        }

        ret = mfvdec_send_packet(avctx, c->pkt);
        if (ret == AVERROR(EAGAIN)) {
            // It has just said it has no output, yet it takes no input.
            if (++c->refused <= MAX_REFUSALS)
                continue;
            av_log(avctx, AV_LOG_ERROR, "the decoder takes neither input nor output\n");
            av_packet_unref(c->pkt);
            c->refused = 0;
            return AVERROR_EXTERNAL;
        }
        c->refused = 0;
        av_packet_unref(c->pkt);
        if (ret < 0)
            return ret;
    }
}

static av_cold void mfvdec_flush(AVCodecContext *avctx)
{
    MFVDecContext *c = avctx->priv_data;

    if (c->mft)
        IMFTransform_ProcessMessage(c->mft, MFT_MESSAGE_COMMAND_FLUSH, 0);
    c->draining      = 0;
    c->refused       = 0;
    c->have_offset   = 0;
    c->discontinuity = 1;
    av_packet_unref(c->pkt);
    mfvdec_drop_all_pending(c);
}

static av_cold int mfvdec_close(AVCodecContext *avctx)
{
    MFVDecContext *c = avctx->priv_data;

    mfvdec_drop_all_pending(c);
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

#define OFFSET(x) offsetof(MFVDecContext, x)
#define VD AV_OPT_FLAG_VIDEO_PARAM | AV_OPT_FLAG_DECODING_PARAM
static const AVOption mfvdec_options[] = {
    { "low_latency", "Pictures out as soon as they are decoded (-1: with one thread or -flags low_delay)",
      OFFSET(low_latency), AV_OPT_TYPE_INT, { .i64 = -1 }, -1, 1, VD },
    { NULL }
};

static const AVClass mfvdec_class = {
    .class_name = "h264_mf decoder",
    .item_name  = av_default_item_name,
    .option     = mfvdec_options,
    .version    = LIBAVUTIL_VERSION_INT,
};

const FFCodec ff_h264_mf_decoder = {
    .p.name         = "h264_mf",
    CODEC_LONG_NAME("H.264 via MediaFoundation"),
    .p.type         = AVMEDIA_TYPE_VIDEO,
    .p.id           = AV_CODEC_ID_H264,
    .p.capabilities = AV_CODEC_CAP_DELAY | AV_CODEC_CAP_DR1 |
                      AV_CODEC_CAP_OTHER_THREADS,
    .p.priv_class   = &mfvdec_class,
    .p.wrapper_name = "mf",
    .priv_data_size = sizeof(MFVDecContext),
    .init           = mfvdec_init,
    .close          = mfvdec_close,
    .flush          = mfvdec_flush,
    FF_CODEC_RECEIVE_FRAME_CB(mfvdec_receive_frame),
    .bsfs           = "h264_mp4toannexb",
    .caps_internal  = FF_CODEC_CAP_INIT_CLEANUP | FF_CODEC_CAP_SETS_FRAME_PROPS,
};
