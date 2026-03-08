package com.eyedeadevelopment.example

import android.graphics.*
import android.media.*
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.nio.ByteBuffer
import kotlin.math.cos
import kotlin.math.PI

class MainActivity : FlutterActivity() {

    companion object {
        private const val CHANNEL = "video_creator"
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            .setMethodCallHandler { call, result ->
                if (call.method == "createVideo") {
                    val audioPath = call.argument<String>("audioPath")!!
                    val videoPath = call.argument<String>("videoPath")!!
                    val text      = call.argument<String>("text") ?: ""
                    Thread {
                        try {
                            VideoCreator.create(audioPath, videoPath, text)
                            result.success(null)
                        } catch (e: Throwable) {  // catches OOM and all errors
                            result.error("VIDEO_ERROR", "${e.javaClass.simpleName}: ${e.message}", null)
                        }
                    }.start()
                } else {
                    result.notImplemented()
                }
            }
    }
}

object VideoCreator {
    private const val W          = 1080
    private const val H          = 1920
    private const val FPS        = 24
    private const val VIDEO_BPS  = 2_000_000
    private const val AUDIO_BPS  = 128_000
    private const val TIMEOUT_US = 10_000L

    private const val TEXT_SIZE_NORMAL    = 38f
    private const val TEXT_SIZE_HIGHLIGHT = 38f   // same size, highlight via background
    private const val LINE_HEIGHT         = 64f
    private const val SENTENCE_GAP        = 24f
    private const val SIDE_PADDING        = 0.82f  // text max-width as fraction of W

    private const val COLOR_BACKGROUND    = "#000000"   // pure black
    private const val COLOR_TEXT          = "#FFFFFF"   // white for all text
    private const val COLOR_HIGHLIGHT_BG  = "#333333"   // dark grey box behind active sentence

    // ── Paints (re-created per render to be thread-safe) ──────────────────
    private fun normalPaint() = Paint().apply {
        color       = Color.parseColor(COLOR_TEXT)
        textSize    = TEXT_SIZE_NORMAL
        isAntiAlias = true
        textAlign   = Paint.Align.CENTER
    }
    private fun highlightPaint() = Paint().apply {
        color       = Color.parseColor(COLOR_TEXT)   // same white text
        textSize    = TEXT_SIZE_HIGHLIGHT
        isAntiAlias = true
        textAlign   = Paint.Align.CENTER
    }
    private fun highlightBgPaint() = Paint().apply {
        color = Color.parseColor(COLOR_HIGHLIGHT_BG)
        style = Paint.Style.FILL
    }

    // ── Sentence layout info ──────────────────────────────────────────────
    private data class SentenceLayout(
        val lines: List<String>,
        val paint: Paint,          // snapshot of the paint used (for drawing)
        val topY: Float,           // Y of first baseline in full-canvas coords
        val bottomY: Float,        // Y below last line
        val centerY: Float         // scroll target: place this at H/2
    )

    // ── Public entry point ────────────────────────────────────────────────
    fun create(audioPath: String, outputPath: String, text: String) {

        // 1. Probe source audio
        val extractor     = MediaExtractor().also { it.setDataSource(audioPath) }
        val audioTrackIdx = (0 until extractor.trackCount).firstOrNull { i ->
            extractor.getTrackFormat(i)
                .getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
        } ?: throw Exception("No audio track found in $audioPath")

        val srcAudioFmt  = extractor.getTrackFormat(audioTrackIdx)
        val durationUs   = srcAudioFmt.getLong(MediaFormat.KEY_DURATION)
        val sampleRate   = srcAudioFmt.getInteger(MediaFormat.KEY_SAMPLE_RATE)
        val channelCount = srcAudioFmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
        val totalFrames  = ((durationUs / 1_000_000.0) * FPS).toInt() + FPS

        // 2. Split into sentences and lay out the full text canvas
        val sentences = splitSentences(text)
        if (sentences.isEmpty()) {
            // Blank video — just encode silence
            val yuvFrame = blankFrame(W, H)
            val (vs, vf) = encodeVideo({ yuvFrame }, totalFrames)
            extractor.selectTrack(audioTrackIdx)
            val (as2, af) = transcodeAudio(extractor, srcAudioFmt, sampleRate, channelCount)
            extractor.release()
            muxToFile(outputPath, vs, vf, as2, af); return
        }

        val layouts = layoutSentences(sentences, W)

        // Build a flat list of every line across all sentences, with its Y position
        data class LineInfo(val sentIdx: Int, val lineIdx: Int, val lineY: Float)
        val allLines = layouts.flatMapIndexed { sIdx, layout ->
            layout.lines.indices.map { lIdx ->
                LineInfo(sIdx, lIdx, layout.topY + lIdx * LINE_HEIGHT)
            }
        }
        val totalLines    = allLines.size.coerceAtLeast(1)
        val framesPerLine = totalFrames.toFloat() / totalLines

        // Scroll target: centre the current line vertically on screen,
        // but never scroll above the top (no blank space at start)
        val lineScrollTargets = allLines.map {
            (it.lineY - H / 2f + LINE_HEIGHT / 2f).coerceAtLeast(0f)
        }

        // Transition: up to 0.3 s, but ≤ half a line's frame budget
        val transFrames = (FPS * 0.3).toInt()
            .coerceAtMost((framesPerLine / 2).toInt())
            .coerceAtLeast(1)

        // 3. Encode H.264 — one highlighted line advances at a time
        var prevScrollY  = lineScrollTargets[0]
        var prevLineIdx  = 0

        val frameProvider: (Int) -> ByteArray = { frameIdx ->
            val lineIdx = ((frameIdx / framesPerLine).toInt())
                .coerceIn(0, totalLines - 1)

            if (lineIdx != prevLineIdx) {
                prevScrollY = lineScrollTargets[prevLineIdx]
                prevLineIdx = lineIdx
            }

            val frameInLine = frameIdx - (lineIdx * framesPerLine).toInt()
            val scrollY = if (frameInLine >= transFrames) {
                lineScrollTargets[lineIdx]
            } else {
                val t = frameInLine.toFloat() / transFrames
                prevScrollY + (lineScrollTargets[lineIdx] - prevScrollY) * easeInOut(t)
            }

            val line = allLines[lineIdx]
            renderScrollFrame(layouts, line.sentIdx, line.lineIdx, scrollY, W, H)
        }

        val (videoSamples, videoFmt) = encodeVideo(frameProvider, totalFrames)

        // 4. Transcode audio: src → PCM → AAC
        extractor.selectTrack(audioTrackIdx)
        val (aacSamples, aacFmt) = transcodeAudio(extractor, srcAudioFmt, sampleRate, channelCount)
        extractor.release()

        // 5. Mux
        muxToFile(outputPath, videoSamples, videoFmt, aacSamples, aacFmt)
    }

    // ── Ease-in-out (cosine) ──────────────────────────────────────────────
    private fun easeInOut(t: Float): Float =
        (1f - cos(t * PI).toFloat()) / 2f

    // ── Layout: measure every sentence in full-canvas coordinates ─────────
    private fun layoutSentences(sentences: List<String>, w: Int): List<SentenceLayout> {
        val maxW    = w * SIDE_PADDING
        // Start near the top so text fills the screen from the beginning
        var curY    = LINE_HEIGHT
        val result  = mutableListOf<SentenceLayout>()

        for ((idx, sentence) in sentences.withIndex()) {
            val paint = if (idx == 0) highlightPaint() else normalPaint()
            val lines = wordWrap(sentence, paint, maxW)
            val blockHeight = lines.size * LINE_HEIGHT
            val topY    = curY
            val bottomY = curY + blockHeight
            val centerY = (topY + bottomY) / 2f
            result.add(SentenceLayout(lines, paint, topY, bottomY, centerY))
            curY = bottomY + SENTENCE_GAP
        }
        return result
    }

    // ── Render one frame at given scrollY ─────────────────────────────────
    private fun renderScrollFrame(
        layouts: List<SentenceLayout>,
        currentSentIdx: Int,
        currentLineIdx: Int,   // which line within the sentence is highlighted
        scrollY: Float,
        w: Int, h: Int
    ): ByteArray {
        val bmp    = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bmp)
        canvas.drawColor(Color.parseColor(COLOR_BACKGROUND))

        val cx     = w / 2f
        val hPad   = 40f   // horizontal padding inside the highlight box
        val vPad   = 8f    // vertical padding inside the highlight box
        val radius = 14f   // rounded corner radius

        for ((idx, layout) in layouts.withIndex()) {
            val screenTop = layout.topY - scrollY
            if (layout.bottomY - scrollY < -LINE_HEIGHT || screenTop > h + LINE_HEIGHT) continue

            val paint = normalPaint()

            var lineY = screenTop + LINE_HEIGHT
            for ((lIdx, line) in layout.lines.withIndex()) {
                if (lineY > -LINE_HEIGHT && lineY < h + LINE_HEIGHT) {
                    // Draw highlight box only behind the single active line
                    if (idx == currentSentIdx && lIdx == currentLineIdx) {
                        canvas.drawRoundRect(
                            hPad, lineY - LINE_HEIGHT + vPad,
                            w - hPad, lineY + vPad,
                            radius, radius,
                            highlightBgPaint()
                        )
                    }
                    canvas.drawText(line, cx, lineY, paint)
                }
                lineY += LINE_HEIGHT
            }
        }

        return bitmapToNv12(bmp, w, h)
    }

    // ── H.264 encoding ────────────────────────────────────────────────────
    private data class Sample(val data: ByteArray, val pts: Long, val flags: Int)

    private fun encodeVideo(
        frameProvider: (Int) -> ByteArray,
        totalFrames: Int
    ): Pair<List<Sample>, MediaFormat> {

        val fmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, W, H).apply {
            setInteger(MediaFormat.KEY_BIT_RATE,         VIDEO_BPS)
            setInteger(MediaFormat.KEY_FRAME_RATE,       FPS)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
            setInteger(MediaFormat.KEY_COLOR_FORMAT,
                MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible)
        }
        val encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        encoder.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        encoder.start()

        val samples    = mutableListOf<Sample>()
        var outFmt: MediaFormat? = null
        var sent       = 0
        var inputDone  = false
        var outputDone = false
        val info       = MediaCodec.BufferInfo()

        while (!outputDone) {
            if (!inputDone) {
                val idx = encoder.dequeueInputBuffer(TIMEOUT_US)
                if (idx >= 0) {
                    val pts = (sent * 1_000_000L) / FPS
                    if (sent >= totalFrames) {
                        encoder.queueInputBuffer(idx, 0, 0, pts,
                            MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        inputDone = true
                    } else {
                        val yuvFrame = frameProvider(sent)
                        encoder.getInputBuffer(idx)!!.also { it.clear(); it.put(yuvFrame) }
                        encoder.queueInputBuffer(idx, 0, yuvFrame.size, pts, 0)
                        sent++
                    }
                }
            }
            val outIdx = encoder.dequeueOutputBuffer(info, TIMEOUT_US)
            when {
                outIdx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED ->
                    outFmt = encoder.outputFormat
                outIdx >= 0 -> {
                    if (info.size > 0 &&
                        (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0) {
                        val chunk = ByteArray(info.size)
                        encoder.getOutputBuffer(outIdx)!!.get(chunk)
                        samples.add(Sample(chunk, info.presentationTimeUs, info.flags))
                    }
                    encoder.releaseOutputBuffer(outIdx, false)
                    if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0)
                        outputDone = true
                }
            }
        }
        encoder.stop(); encoder.release()
        return Pair(samples, outFmt!!)
    }

    // ── Audio transcode: any format → PCM → AAC ──────────────────────────
    private fun transcodeAudio(
        extractor: MediaExtractor,
        srcFmt: MediaFormat,
        sampleRate: Int,
        channelCount: Int
    ): Pair<List<Sample>, MediaFormat> {

        // Phase 1: decode ALL compressed audio → raw PCM
        val decoder = MediaCodec.createDecoderByType(
            srcFmt.getString(MediaFormat.KEY_MIME)!!)
        decoder.configure(srcFmt, null, null, 0)
        decoder.start()

        val pcmAccumulator = java.io.ByteArrayOutputStream()
        var decInputDone   = false
        var decOutputDone  = false
        val info           = MediaCodec.BufferInfo()

        while (!decOutputDone) {
            if (!decInputDone) {
                val idx = decoder.dequeueInputBuffer(TIMEOUT_US)
                if (idx >= 0) {
                    val buf  = decoder.getInputBuffer(idx)!!
                    buf.clear()
                    val size = extractor.readSampleData(buf, 0)
                    if (size < 0) {
                        decoder.queueInputBuffer(idx, 0, 0, 0,
                            MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        decInputDone = true
                    } else {
                        decoder.queueInputBuffer(idx, 0, size,
                            extractor.sampleTime, 0)
                        extractor.advance()
                    }
                }
            }
            val outIdx = decoder.dequeueOutputBuffer(info, TIMEOUT_US)
            if (outIdx >= 0) {
                if (info.size > 0) {
                    val pcm = ByteArray(info.size)
                    decoder.getOutputBuffer(outIdx)!!.get(pcm)
                    pcmAccumulator.write(pcm)
                }
                decoder.releaseOutputBuffer(outIdx, false)
                if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0)
                    decOutputDone = true
            }
        }
        decoder.stop(); decoder.release()

        val pcmBytes       = pcmAccumulator.toByteArray()
        val bytesPerSample = 2 * channelCount
        val bytesPerSec    = sampleRate * bytesPerSample.toLong()

        // Phase 2: encode PCM → AAC in encoder-sized chunks
        val aacFmt = MediaFormat.createAudioFormat(
            MediaFormat.MIMETYPE_AUDIO_AAC, sampleRate, channelCount).apply {
            setInteger(MediaFormat.KEY_BIT_RATE, AUDIO_BPS)
            setInteger(MediaFormat.KEY_AAC_PROFILE,
                MediaCodecInfo.CodecProfileLevel.AACObjectLC)
        }
        val encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC)
        encoder.configure(aacFmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        encoder.start()

        val aacSamples    = mutableListOf<Sample>()
        var outFmt: MediaFormat? = null
        var pcmOffset     = 0
        var encInputDone  = false
        var encOutputDone = false

        while (!encOutputDone) {
            if (!encInputDone) {
                val idx = encoder.dequeueInputBuffer(TIMEOUT_US)
                if (idx >= 0) {
                    val buf       = encoder.getInputBuffer(idx)!!
                    buf.clear()
                    val remaining = pcmBytes.size - pcmOffset
                    if (remaining <= 0) {
                        encoder.queueInputBuffer(idx, 0, 0, 0,
                            MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        encInputDone = true
                    } else {
                        val toPut = minOf(remaining, buf.remaining())
                        buf.put(pcmBytes, pcmOffset, toPut)
                        val pts = (pcmOffset.toLong() * 1_000_000L) / bytesPerSec
                        encoder.queueInputBuffer(idx, 0, toPut, pts, 0)
                        pcmOffset += toPut
                    }
                }
            }
            val outIdx = encoder.dequeueOutputBuffer(info, TIMEOUT_US)
            when {
                outIdx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED ->
                    outFmt = encoder.outputFormat
                outIdx >= 0 -> {
                    if (info.size > 0 &&
                        (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0) {
                        val chunk = ByteArray(info.size)
                        encoder.getOutputBuffer(outIdx)!!.get(chunk)
                        aacSamples.add(Sample(chunk, info.presentationTimeUs, info.flags))
                    }
                    encoder.releaseOutputBuffer(outIdx, false)
                    if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0)
                        encOutputDone = true
                }
            }
        }
        encoder.stop(); encoder.release()
        return Pair(aacSamples, outFmt!!)
    }

    // ── Mux helper ────────────────────────────────────────────────────────
    private fun muxToFile(
        outputPath: String,
        videoSamples: List<Sample>, videoFmt: MediaFormat,
        aacSamples: List<Sample>,   aacFmt: MediaFormat
    ) {
        File(outputPath).parentFile?.mkdirs()
        val muxer      = MediaMuxer(outputPath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        val videoTrack = muxer.addTrack(videoFmt)
        val audioTrack = muxer.addTrack(aacFmt)
        muxer.start()
        val info = MediaCodec.BufferInfo()
        for (s in videoSamples) {
            info.set(0, s.data.size, s.pts, s.flags)
            muxer.writeSampleData(videoTrack, ByteBuffer.wrap(s.data), info)
        }
        for (s in aacSamples) {
            info.set(0, s.data.size, s.pts, s.flags)
            muxer.writeSampleData(audioTrack, ByteBuffer.wrap(s.data), info)
        }
        muxer.stop(); muxer.release()
    }

    // ── Sentence splitting ────────────────────────────────────────────────
    private fun splitSentences(text: String): List<String> {
        if (text.isBlank()) return emptyList()
        return text.trim()
            .split(Regex("(?<=[.!?])\\s+"))
            .map { it.trim() }
            .filter { it.isNotBlank() }
    }

    // ── Word wrap ─────────────────────────────────────────────────────────
    private fun wordWrap(text: String, paint: Paint, maxW: Float): List<String> {
        val words = text.trim().split(Regex("\\s+"))
        val lines = mutableListOf<String>()
        var cur   = ""
        for (w in words) {
            val test = if (cur.isEmpty()) w else "$cur $w"
            if (paint.measureText(test) <= maxW) cur = test
            else { if (cur.isNotEmpty()) lines.add(cur); cur = w }
        }
        if (cur.isNotEmpty()) lines.add(cur)
        return lines
    }

    // ── Blank frame ───────────────────────────────────────────────────────
    private fun blankFrame(w: Int, h: Int): ByteArray {
        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        Canvas(bmp).drawColor(Color.parseColor(COLOR_BACKGROUND))
        return bitmapToNv12(bmp, w, h)
    }

    // ── NV12 conversion ───────────────────────────────────────────────────
    private fun bitmapToNv12(bmp: Bitmap, w: Int, h: Int): ByteArray {
        val yuv    = ByteArray(w * h * 3 / 2)
        val pixels = IntArray(w * h)
        bmp.getPixels(pixels, 0, w, 0, 0, w, h)
        var yi = 0; var uvi = w * h
        for (j in 0 until h) {
            for (i in 0 until w) {
                val p = pixels[j * w + i]
                val r = (p shr 16) and 0xff
                val g = (p shr 8) and 0xff
                val b = p and 0xff
                yuv[yi++] = (((66*r + 129*g + 25*b + 128) shr 8) + 16)
                    .coerceIn(16, 235).toByte()
                if (j % 2 == 0 && i % 2 == 0) {
                    yuv[uvi++] = (((-38*r - 74*g + 112*b + 128) shr 8) + 128)
                        .coerceIn(16, 240).toByte()
                    yuv[uvi++] = (((112*r - 94*g - 18*b + 128) shr 8) + 128)
                        .coerceIn(16, 240).toByte()
                }
            }
        }
        return yuv
    }
}
