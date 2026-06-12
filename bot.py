import os
import logging
import subprocess
import tempfile
import shutil

try:
    import imageio_ffmpeg
    _ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    os.environ["PATH"] = os.path.dirname(_ffmpeg_path) + ":" + os.environ.get("PATH", "")
except Exception as e:
    logging.warning(f"imageio_ffmpeg not available: {e}")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "")
RAILWAY_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
PORT = int(os.environ.get("PORT", 8080))
COOKIES_FILE = "/app/cookies.txt"

# Храним обложки в памяти как Telegram file_id (не сбрасывается при деплое внутри сессии)
# Для постоянного хранения используем файл
COVERS_DIR = "/app/user_covers"
os.makedirs(COVERS_DIR, exist_ok=True)

# Кэш в памяти: user_id -> file_id
_cover_cache: dict = {}

def get_cover_file_id(user_id: int):
    """Возвращает сохранённый Telegram file_id обложки или None"""
    if user_id in _cover_cache:
        return _cover_cache[user_id]
    # Пробуем загрузить из файла
    path = os.path.join(COVERS_DIR, f"{user_id}.txt")
    if os.path.exists(path):
        with open(path, "r") as f:
            fid = f.read().strip()
            _cover_cache[user_id] = fid
            return fid
    return None

def save_cover_file_id(user_id: int, file_id: str):
    """Сохраняет Telegram file_id обложки"""
    _cover_cache[user_id] = file_id
    path = os.path.join(COVERS_DIR, f"{user_id}.txt")
    with open(path, "w") as f:
        f.write(file_id)

def delete_cover(user_id: int):
    """Удаляет сохранённую обложку"""
    _cover_cache.pop(user_id, None)
    path = os.path.join(COVERS_DIR, f"{user_id}.txt")
    if os.path.exists(path):
        os.remove(path)

def user_has_cover(user_id: int) -> bool:
    return get_cover_file_id(user_id) is not None

def parse_time(time_str: str) -> int:
    """Парсит время в секунды. Форматы: 1:23, 1:23:45, 83 (секунды)"""
    time_str = time_str.strip()
    try:
        parts = time_str.split(":")
        if len(parts) == 1:
            return int(parts[0])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except:
        return -1
    return -1

def seconds_to_str(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я MP3 Tools Bot.\n\n"
        "Я умею:\n"
        "🎬 *Видео → MP3* — отправь видео, я извлеку звук\n"
        "🎤 *Видео → голосовое* — отправь видео, конвертирую в голосовое\n"
        "🏷️ *Теги MP3* — отправь аудио, изменю название, исполнителя и обложку\n"
        "✂️ *Обрезка аудио* — вырежи нужный фрагмент в MP3 или голосовое\n"
        "▶️ *YouTube → MP3* — отправь ссылку YouTube, скачаю аудио\n\n"
        "🖼️ *Обложка по умолчанию:*\n"
        "/setcover — установить обложку\n"
        "/mycover — посмотреть обложку\n"
        "/removecover — удалить обложку\n\n"
        "Просто отправь файл или ссылку!",
        parse_mode="Markdown"
    )

async def cmd_setcover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting_for_cover"] = True
    await update.message.reply_text("🖼️ Отправь фото для обложки по умолчанию:")

async def cmd_changecover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting_for_cover"] = True
    await update.message.reply_text("🖼️ Отправь новое фото для замены обложки:")

async def cmd_mycover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    fid = get_cover_file_id(user_id)
    if fid:
        await update.message.reply_photo(
            photo=fid,
            caption="🖼️ Твоя текущая обложка.\n\n"
                    "Заменить: /setcover\n"
                    "Удалить: /removecover"
        )
    else:
        await update.message.reply_text("❌ Нет сохранённой обложки. Установи через /setcover")

async def cmd_removecover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_has_cover(user_id):
        delete_cover(user_id)
        await update.message.reply_text("✅ Обложка удалена.")
    else:
        await update.message.reply_text("❌ Нет сохранённой обложки.")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.video:
        file_id = update.message.video.file_id
    elif update.message.document and update.message.document.mime_type and "video" in update.message.document.mime_type:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("❌ Не удалось распознать видео.")
        return

    context.user_data["video_file_id"] = file_id
    keyboard = [
        [InlineKeyboardButton("🎵 Извлечь MP3", callback_data="extract_mp3")],
        [InlineKeyboardButton("🎤 Голосовое сообщение", callback_data="extract_voice")],
    ]
    await update.message.reply_text(
        "🎬 Видео получено! Что сделать?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.audio:
        file_id = update.message.audio.file_id
        fname = update.message.audio.file_name or "audio.mp3"
        duration = update.message.audio.duration or 0
    elif update.message.document and update.message.document.mime_type and "audio" in update.message.document.mime_type:
        file_id = update.message.document.file_id
        fname = update.message.document.file_name or "audio.mp3"
        duration = 0
    else:
        await update.message.reply_text("❌ Не удалось распознать аудио.")
        return

    context.user_data["audio_file_id"] = file_id
    context.user_data["audio_fname"] = fname
    context.user_data["audio_duration"] = duration

    user_id = update.message.from_user.id
    has_cover = user_has_cover(user_id)
    cover_hint = "🖼️ Обложка по умолчанию будет добавлена автоматически." if has_cover else "💡 Нет обложки. Установи через /setcover"

    duration_str = f" ({seconds_to_str(duration)})" if duration else ""

    keyboard = [
        [InlineKeyboardButton("🏷️ Изменить теги", callback_data="edit_tags")],
        [InlineKeyboardButton("✂️ Обрезать аудио", callback_data="trim_audio")],
    ]
    await update.message.reply_text(
        f"🎵 Аудио получено: *{fname}*{duration_str}\n{cover_hint}\n\nЧто сделать?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if "youtube.com" in text or "youtu.be" in text:
        await handle_youtube(update, context, text)
        return

    # Обрезка — ввод времени начала
    if context.user_data.get("trim_step") == "start":
        seconds = parse_time(text)
        if seconds < 0:
            await update.message.reply_text(
                "❌ Неверный формат. Введи время в формате:\n"
                "• *1:30* (1 минута 30 секунд)\n"
                "• *90* (90 секунд)\n"
                "• *1:30:00* (1 час 30 минут)",
                parse_mode="Markdown"
            )
            return
        context.user_data["trim_start"] = seconds
        context.user_data["trim_step"] = "end"
        duration = context.user_data.get("audio_duration", 0)
        duration_hint = f"\nДлина аудио: *{seconds_to_str(duration)}*" if duration else ""
        await update.message.reply_text(
            f"✂️ Начало: *{seconds_to_str(seconds)}*{duration_hint}\n\n"
            "Теперь введи *время конца* (или напиши 'конец' чтобы до конца файла):",
            parse_mode="Markdown"
        )
        return

    # Обрезка — ввод времени конца
    if context.user_data.get("trim_step") == "end":
        if text.lower() in ["конец", "end", "до конца"]:
            context.user_data["trim_end"] = None
        else:
            seconds = parse_time(text)
            if seconds < 0:
                await update.message.reply_text(
                    "❌ Неверный формат. Введи время или напиши *конец*:",
                    parse_mode="Markdown"
                )
                return
            start = context.user_data.get("trim_start", 0)
            if seconds <= start:
                await update.message.reply_text(
                    f"❌ Время конца должно быть больше начала ({seconds_to_str(start)}). Попробуй ещё раз:"
                )
                return
            context.user_data["trim_end"] = seconds

        context.user_data["trim_step"] = None
        end = context.user_data.get("trim_end")
        start = context.user_data.get("trim_start", 0)
        end_str = seconds_to_str(end) if end else "конец"

        keyboard = [
            [InlineKeyboardButton("🎵 Сохранить как MP3", callback_data="trim_mp3")],
            [InlineKeyboardButton("🎤 Сохранить как голосовое", callback_data="trim_voice")],
        ]
        await update.message.reply_text(
            f"✂️ Фрагмент: *{seconds_to_str(start)}* → *{end_str}*\n\nКак сохранить?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    step = context.user_data.get("tag_step")
    if not step:
        await update.message.reply_text("Отправь мне видео, аудио или ссылку YouTube!")
        return

    if text == "-":
        text = None

    if step == "title":
        context.user_data["tag_title"] = text
        context.user_data["tag_step"] = "artist"
        await update.message.reply_text("✏️ Введи *исполнителя* (или '-' чтобы пропустить):", parse_mode="Markdown")
    elif step == "artist":
        context.user_data["tag_artist"] = text
        has_cover = user_has_cover(update.message.from_user.id)
        if has_cover:
            # Есть сохранённая обложка — сразу применяем без вопросов
            context.user_data["tag_use_saved_cover"] = True
            context.user_data["tag_cover_file_id"] = None
            context.user_data["tag_step"] = None
            await update.message.reply_text("⏳ Применяю теги с сохранённой обложкой...")
            await do_apply_tags(update, context)
        else:
            # Нет обложки — спрашиваем
            context.user_data["tag_step"] = "cover"
            await update.message.reply_text(
                "🖼️ Отправь *фото* для обложки (или '-' чтобы пропустить):",
                parse_mode="Markdown"
            )
    elif step == "cover":
        if text == "-":
            user_id = update.message.from_user.id
            context.user_data["tag_use_saved_cover"] = user_has_cover(user_id)
            context.user_data["tag_cover_file_id"] = None
            context.user_data["tag_step"] = None
            await update.message.reply_text("⏳ Применяю теги...")
            await do_apply_tags(update, context)
        elif text == "нет":
            context.user_data["tag_cover_file_id"] = None
            context.user_data["tag_use_saved_cover"] = False
            context.user_data["tag_step"] = None
            await update.message.reply_text("⏳ Применяю теги...")
            await do_apply_tags(update, context)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if context.user_data.get("waiting_for_cover"):
        context.user_data["waiting_for_cover"] = False
        photo = update.message.photo[-1]
        save_cover_file_id(user_id, photo.file_id)
        await update.message.reply_text(
            "✅ Обложка по умолчанию сохранена!\n"
            "Посмотреть: /mycover | Удалить: /removecover"
        )
        return

    step = context.user_data.get("tag_step")
    if step == "cover":
        photo = update.message.photo[-1]
        context.user_data["tag_cover_file_id"] = photo.file_id
        context.user_data["tag_use_saved_cover"] = False
        context.user_data["tag_step"] = None
        await update.message.reply_text("⏳ Применяю теги и обложку...")
        await do_apply_tags(update, context)
        return

    await update.message.reply_text("Отправь мне видео или аудио файл!")

async def handle_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    msg = await update.message.reply_text("⏳ Скачиваю аудио с YouTube...")
    logger.info(f"Cookies file exists: {os.path.exists(COOKIES_FILE)}")
    try:
        import yt_dlp
        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": output_template,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "quiet": False,
            }
            if os.path.exists(COOKIES_FILE):
                ydl_opts["cookiefile"] = COOKIES_FILE

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "audio")

            mp3_file = None
            for f in os.listdir(tmpdir):
                if f.endswith(".mp3"):
                    mp3_file = os.path.join(tmpdir, f)
                    break

            if not mp3_file:
                await msg.edit_text("❌ Не удалось найти скачанный файл.")
                return

            await msg.edit_text(f"📤 Отправляю: *{title}*...", parse_mode="Markdown")
            with open(mp3_file, "rb") as f:
                await update.message.reply_audio(
                    audio=f, title=title,
                    filename=f"{title}.mp3", caption="✅ Готово!"
                )
            await msg.delete()

    except Exception as e:
        logger.error(f"YouTube error: {e}")
        await msg.edit_text("❌ Ошибка при скачивании. Проверь ссылку и попробуй ещё раз.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "extract_mp3":
        await query.edit_message_text("⏳ Конвертирую в MP3, подожди...")
        await do_extract(update, context, voice=False)
    elif data == "extract_voice":
        await query.edit_message_text("⏳ Конвертирую в голосовое, подожди...")
        await do_extract(update, context, voice=True)
    elif data == "edit_tags":
        await query.edit_message_text("✏️ Введи *название песни* (или '-' чтобы пропустить):", parse_mode="Markdown")
        context.user_data["tag_step"] = "title"
    elif data == "trim_audio":
        await query.edit_message_text(
            "✂️ *Обрезка аудио*\n\n"
            "Введи *время начала* в формате:\n"
            "• *1:30* (1 минута 30 секунд)\n"
            "• *90* (90 секунд)\n"
            "• *0* (с самого начала)",
            parse_mode="Markdown"
        )
        context.user_data["trim_step"] = "start"
    elif data == "trim_mp3":
        await query.edit_message_text("⏳ Обрезаю и сохраняю как MP3...")
        await do_trim(update, context, voice=False)
    elif data == "trim_voice":
        await query.edit_message_text("⏳ Обрезаю и сохраняю как голосовое...")
        await do_trim(update, context, voice=True)

def get_ffmpeg():
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return "ffmpeg"

async def do_trim(update: Update, context: ContextTypes.DEFAULT_TYPE, voice: bool):
    query = update.callback_query
    file_id = context.user_data.get("audio_file_id")
    start = context.user_data.get("trim_start", 0)
    end = context.user_data.get("trim_end")

    if not file_id:
        await query.message.reply_text("❌ Файл не найден, отправь аудио заново.")
        return

    try:
        file = await context.bot.get_file(file_id)
        ffmpeg = get_ffmpeg()

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.mp3")
            await file.download_to_drive(input_path)

            duration_args = ["-ss", str(start)]
            if end:
                duration_args += ["-to", str(end)]

            if voice:
                output_path = os.path.join(tmpdir, "trimmed.ogg")
                cmd = [ffmpeg, "-y", *duration_args, "-i", input_path,
                       "-acodec", "libopus", "-b:a", "64k",
                       "-ar", "48000", "-ac", "1", output_path]
            else:
                output_path = os.path.join(tmpdir, "trimmed.mp3")
                cmd = [ffmpeg, "-y", *duration_args, "-i", input_path,
                       "-acodec", "mp3", "-ab", "192k", output_path]

            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                raise Exception(f"ffmpeg error: {result.stderr.decode()}")

            end_str = seconds_to_str(end) if end else "конец"
            caption = f"✂️ Фрагмент: {seconds_to_str(start)} → {end_str}"

            if voice:
                with open(output_path, "rb") as f:
                    await query.message.reply_voice(voice=f, caption=caption)
            else:
                with open(output_path, "rb") as f:
                    await query.message.reply_audio(
                        audio=f, filename="trimmed.mp3", caption=caption
                    )

    except subprocess.TimeoutExpired:
        await query.message.reply_text("❌ Файл слишком большой.")
    except Exception as e:
        logger.error(f"Trim error: {e}")
        await query.message.reply_text("❌ Ошибка при обрезке.")
    finally:
        for key in ["trim_start", "trim_end", "trim_step"]:
            context.user_data.pop(key, None)

async def do_extract(update: Update, context: ContextTypes.DEFAULT_TYPE, voice: bool):
    query = update.callback_query
    file_id = context.user_data.get("video_file_id")
    if not file_id:
        await query.message.reply_text("❌ Файл не найден, отправь видео заново.")
        return

    try:
        file = await context.bot.get_file(file_id)
        ffmpeg = get_ffmpeg()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "input.mp4")
            await file.download_to_drive(video_path)

            if voice:
                output_path = os.path.join(tmpdir, "output.ogg")
                cmd = [ffmpeg, "-y", "-i", video_path,
                       "-vn", "-acodec", "libopus",
                       "-b:a", "64k", "-ar", "48000", "-ac", "1", output_path]
            else:
                output_path = os.path.join(tmpdir, "output.mp3")
                cmd = [ffmpeg, "-y", "-i", video_path,
                       "-vn", "-acodec", "mp3", "-ab", "192k", output_path]

            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                raise Exception(f"ffmpeg error: {result.stderr.decode()}")

            if voice:
                try:
                    with open(output_path, "rb") as f:
                        await query.message.reply_voice(voice=f, caption="🎤 Голосовое готово!")
                except Exception as voice_err:
                    if "forbidden" in str(voice_err).lower():
                        # Пользователь запретил голосовые — отправляем как аудио
                        with open(output_path, "rb") as f:
                            await query.message.reply_audio(
                                audio=f, filename="audio.ogg",
                                caption="🎵 Голосовые запрещены в настройках — отправляю как аудио файл"
                            )
                    else:
                        raise voice_err
            else:
                with open(output_path, "rb") as f:
                    await query.message.reply_audio(audio=f, filename="audio.mp3", caption="🎵 MP3 готов!")

    except subprocess.TimeoutExpired:
        await query.message.reply_text("❌ Файл слишком большой.")
    except Exception as e:
        logger.error(f"Error in do_extract: {e}")
        await query.message.reply_text("❌ Ошибка при конвертации.")

async def do_apply_tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    file_id = context.user_data.get("audio_file_id")
    fname = context.user_data.get("audio_fname", "audio.mp3")
    title = context.user_data.get("tag_title")
    artist = context.user_data.get("tag_artist")
    cover_file_id = context.user_data.get("tag_cover_file_id")
    use_saved_cover = context.user_data.get("tag_use_saved_cover", True)

    if not file_id:
        await update.message.reply_text("❌ Аудио не найдено, отправь файл заново.")
        return

    try:
        ffmpeg = get_ffmpeg()
        audio_file = await context.bot.get_file(file_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Сохраняем оригинальный файл с его расширением
            orig_ext = os.path.splitext(fname)[1].lower() or ".mp3"
            input_path = os.path.join(tmpdir, f"input{orig_ext}")
            output_path = os.path.join(tmpdir, "output.mp3")
            await audio_file.download_to_drive(input_path)

            meta_args = []
            if title:
                meta_args += ["-metadata", f"title={title}"]
            if artist:
                meta_args += ["-metadata", f"artist={artist}"]

            cover_path = None
            cover_source = None

            if cover_file_id:
                cover_file = await context.bot.get_file(cover_file_id)
                cover_path = os.path.join(tmpdir, "cover.jpg")
                await cover_file.download_to_drive(cover_path)
                cover_source = "новая"
            elif use_saved_cover and user_has_cover(user_id):
                # Скачиваем обложку по file_id
                saved_fid = get_cover_file_id(user_id)
                cover_file = await context.bot.get_file(saved_fid)
                cover_path = os.path.join(tmpdir, "cover.jpg")
                await cover_file.download_to_drive(cover_path)
                cover_source = "сохранённая"

            if cover_path:
                # Сначала конвертируем в mp3, потом добавляем обложку
                converted_path = os.path.join(tmpdir, "converted.mp3")
                cmd_convert = [ffmpeg, "-y", "-i", input_path,
                               "-acodec", "libmp3lame", "-ab", "192k",
                               *meta_args, converted_path]
                result = subprocess.run(cmd_convert, capture_output=True, timeout=180)
                if result.returncode != 0:
                    raise Exception(result.stderr.decode())

                # Добавляем обложку к mp3
                cmd = [ffmpeg, "-y", "-i", converted_path, "-i", cover_path,
                       "-map", "0:a", "-map", "1:v", "-c:a", "copy", "-c:v", "mjpeg",
                       "-id3v2_version", "3",
                       "-metadata:s:v", "title=Album cover",
                       "-metadata:s:v", "comment=Cover (front)",
                       output_path]
            else:
                # Просто конвертируем в mp3 с тегами
                cmd = [ffmpeg, "-y", "-i", input_path,
                       "-acodec", "libmp3lame", "-ab", "192k",
                       *meta_args, output_path]

            result = subprocess.run(cmd, capture_output=True, timeout=180)
            if result.returncode != 0:
                raise Exception(result.stderr.decode())

            caption_parts = ["✅ Готово!"]
            if title:
                caption_parts.append(f"🎵 Название: {title}")
            if artist:
                caption_parts.append(f"👤 Исполнитель: {artist}")
            if cover_path:
                caption_parts.append(f"🖼️ Обложка: {cover_source}")

            out_name = fname if fname.endswith(".mp3") else "output.mp3"
            with open(output_path, "rb") as f:
                await update.message.reply_audio(audio=f, filename=out_name, caption="\n".join(caption_parts))

    except Exception as e:
        logger.error(f"Error in do_apply_tags: {e}")
        await update.message.reply_text("❌ Ошибка при обработке файла.")
    finally:
        for key in ["audio_file_id", "audio_fname", "tag_title", "tag_artist",
                    "tag_cover_file_id", "tag_step", "tag_use_saved_cover"]:
            context.user_data.pop(key, None)

def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set!")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setcover", cmd_setcover))
    app.add_handler(CommandHandler("mycover", cmd_mycover))
    app.add_handler(CommandHandler("removecover", cmd_removecover))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.Document.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_handler))

    if RAILWAY_URL:
        logger.info(f"Starting webhook on {RAILWAY_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"https://{RAILWAY_URL}/webhook",
            url_path="/webhook",
        )
    else:
        logger.info("Starting polling (local mode)...")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
