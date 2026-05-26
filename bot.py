import os
import logging
import imageio
import subprocess
import tempfile
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "")

# States for conversation
WAITING_TITLE = 1
WAITING_ARTIST = 2
WAITING_COVER = 3

user_data_store = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я MP3 Tools Bot.\n\n"
        "Я умею:\n"
        "🎬 *Видео → аудио* — отправь видео, я извлеку звук в формате MP3\n"
        "🎤 *Видео → голосовое* — отправь видео, я конвертирую в голосовое сообщение\n"
        "🏷️ *Теги MP3* — отправь аудио, я помогу изменить название, исполнителя и обложку\n\n"
        "Просто отправь мне файл — и я предложу варианты!",
        parse_mode="Markdown"
    )

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video files"""
    msg = await update.message.reply_text("⏳ Получил видео, выбери формат:")
    
    # Store file_id
    if update.message.video:
        file_id = update.message.video.file_id
    elif update.message.document and update.message.document.mime_type and "video" in update.message.document.mime_type:
        file_id = update.message.document.file_id
    else:
        await msg.edit_text("❌ Не удалось распознать видео.")
        return

    context.user_data["video_file_id"] = file_id

    keyboard = [
        [InlineKeyboardButton("🎵 Извлечь MP3", callback_data="extract_mp3")],
        [InlineKeyboardButton("🎤 Конвертировать в голосовое", callback_data="extract_voice")],
    ]
    await msg.edit_text(
        "🎬 Видео получено! Что сделать?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle audio files"""
    if update.message.audio:
        file_id = update.message.audio.file_id
        fname = update.message.audio.file_name or "audio.mp3"
    elif update.message.document and update.message.document.mime_type and "audio" in update.message.document.mime_type:
        file_id = update.message.document.file_id
        fname = update.message.document.file_name or "audio.mp3"
    else:
        await update.message.reply_text("❌ Не удалось распознать аудио.")
        return

    context.user_data["audio_file_id"] = file_id
    context.user_data["audio_fname"] = fname

    keyboard = [
        [InlineKeyboardButton("🏷️ Изменить теги (название, исполнитель, обложка)", callback_data="edit_tags")],
    ]
    await update.message.reply_text(
        f"🎵 Аудио получено: *{fname}*\nЧто сделать?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "extract_mp3":
        await query.edit_message_text("⏳ Конвертирую в MP3, подожди...")
        await do_extract(update, context, voice=False)

    elif data == "extract_voice":
        await query.edit_message_text("⏳ Конвертирую в голосовое сообщение, подожди...")
        await do_extract(update, context, voice=True)

    elif data == "edit_tags":
        await query.edit_message_text("✏️ Введи *название песни* (или напиши '-' чтобы пропустить):", parse_mode="Markdown")
        context.user_data["tag_step"] = "title"

async def do_extract(update: Update, context: ContextTypes.DEFAULT_TYPE, voice: bool):
    query = update.callback_query
    file_id = context.user_data.get("video_file_id")
    if not file_id:
        await query.message.reply_text("❌ Файл не найден, отправь видео заново.")
        return

    try:
        file = await context.bot.get_file(file_id)
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "input.mp4")
            await file.download_to_drive(video_path)

            if voice:
                output_path = os.path.join(tmpdir, "output.ogg")
                cmd = [
                    "ffmpeg", "-y", "-i", video_path,
                    "-vn", "-acodec", "libopus",
                    "-b:a", "64k", "-ar", "48000", "-ac", "1",
                    output_path
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    raise Exception(f"ffmpeg error: {result.stderr.decode()}")
                with open(output_path, "rb") as f:
                    await query.message.reply_voice(voice=f, caption="🎤 Голосовое сообщение готово!")
            else:
                output_path = os.path.join(tmpdir, "output.mp3")
                cmd = [
                    "ffmpeg", "-y", "-i", video_path,
                    "-vn", "-acodec", "mp3", "-ab", "192k",
                    output_path
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    raise Exception(f"ffmpeg error: {result.stderr.decode()}")
                with open(output_path, "rb") as f:
                    await query.message.reply_audio(audio=f, filename="audio.mp3", caption="🎵 MP3 готов!")

    except subprocess.TimeoutExpired:
        await query.message.reply_text("❌ Файл слишком большой или долго обрабатывается. Попробуй видео покороче.")
    except Exception as e:
        logger.error(f"Error in do_extract: {e}")
        await query.message.reply_text(f"❌ Ошибка при конвертации. Попробуй ещё раз.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for tag editing"""
    step = context.user_data.get("tag_step")
    if not step:
        await update.message.reply_text("Отправь мне видео или аудио файл, и я помогу!")
        return

    text = update.message.text.strip()
    if text == "-":
        text = None

    if step == "title":
        context.user_data["tag_title"] = text
        context.user_data["tag_step"] = "artist"
        await update.message.reply_text("✏️ Введи *исполнителя* (или '-' чтобы пропустить):", parse_mode="Markdown")

    elif step == "artist":
        context.user_data["tag_artist"] = text
        context.user_data["tag_step"] = "cover"
        await update.message.reply_text(
            "🖼️ Отправь *фото* для обложки (или напиши '-' чтобы пропустить):",
            parse_mode="Markdown"
        )

    elif step == "cover" and text == "-":
        context.user_data["tag_cover"] = None
        context.user_data["tag_step"] = None
        await update.message.reply_text("⏳ Применяю теги...")
        await do_apply_tags(update, context)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo for cover art"""
    step = context.user_data.get("tag_step")
    if step != "cover":
        await update.message.reply_text("Отправь мне видео или аудио файл, и я помогу!")
        return

    photo = update.message.photo[-1]  # highest resolution
    context.user_data["tag_cover_file_id"] = photo.file_id
    context.user_data["tag_step"] = None
    await update.message.reply_text("⏳ Применяю теги и обложку...")
    await do_apply_tags(update, context)

async def do_apply_tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = context.user_data.get("audio_file_id")
    fname = context.user_data.get("audio_fname", "audio.mp3")
    title = context.user_data.get("tag_title")
    artist = context.user_data.get("tag_artist")
    cover_file_id = context.user_data.get("tag_cover_file_id")

    if not file_id:
        await update.message.reply_text("❌ Аудио не найдено, отправь файл заново.")
        return

    try:
        audio_file = await context.bot.get_file(file_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.mp3")
            output_path = os.path.join(tmpdir, fname if fname.endswith(".mp3") else "output.mp3")
            await audio_file.download_to_drive(input_path)

            meta_args = []
            if title:
                meta_args += ["-metadata", f"title={title}"]
            if artist:
                meta_args += ["-metadata", f"artist={artist}"]

            if cover_file_id:
                cover_file = await context.bot.get_file(cover_file_id)
                cover_path = os.path.join(tmpdir, "cover.jpg")
                await cover_file.download_to_drive(cover_path)

                cmd = [
                    "ffmpeg", "-y",
                    "-i", input_path,
                    "-i", cover_path,
                    "-map", "0:a", "-map", "1:v",
                    "-c:a", "copy", "-c:v", "mjpeg",
                    "-id3v2_version", "3",
                    "-metadata:s:v", "title=Album cover",
                    "-metadata:s:v", "comment=Cover (front)",
                    *meta_args,
                    output_path
                ]
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-i", input_path,
                    "-c:a", "copy",
                    *meta_args,
                    output_path
                ]

            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode != 0:
                raise Exception(result.stderr.decode())

            out_name = fname if fname.endswith(".mp3") else "output.mp3"
            caption_parts = ["✅ Готово!"]
            if title:
                caption_parts.append(f"🎵 Название: {title}")
            if artist:
                caption_parts.append(f"👤 Исполнитель: {artist}")
            if cover_file_id:
                caption_parts.append("🖼️ Обложка добавлена")

            with open(output_path, "rb") as f:
                await update.message.reply_audio(
                    audio=f,
                    filename=out_name,
                    caption="\n".join(caption_parts)
                )

    except Exception as e:
        logger.error(f"Error in do_apply_tags: {e}")
        await update.message.reply_text("❌ Ошибка при обработке файла. Попробуй ещё раз.")

    finally:
        # Clean up user data
        for key in ["audio_file_id", "audio_fname", "tag_title", "tag_artist", "tag_cover", "tag_cover_file_id", "tag_step"]:
            context.user_data.pop(key, None)

def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set!")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(
        filters.VIDEO | (filters.Document.ALL & filters.Document.MimeType("video/")),
        handle_video
    ))
    app.add_handler(MessageHandler(
        filters.AUDIO | (filters.Document.ALL & filters.Document.MimeType("audio/")),
        handle_audio
    ))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
