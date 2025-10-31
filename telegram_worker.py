import asyncio
import mimetypes
import os
import secrets
import sys
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal
from yt_dlp import YoutubeDL

from download_core import (
    estimate_size,
    human_size,
    list_video_formats,
    normalize_yt,
    pick_best_audio,
)


def _ptb_major_minor() -> Tuple[int, int]:
    try:
        import telegram as tg

        parts = tg.__version__.split(".")[:2]
        return int(parts[0]), int(parts[1])
    except Exception:
        return 20, 0


class TelegramWorker(QThread):
    sig_download_requested = Signal(str, str, object, str)
    sig_info = Signal(str)

    def __init__(self, app_config: dict, parent=None):
        super().__init__(parent)
        self.app_config = app_config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_evt: asyncio.Event | None = None
        self.app: "Application | None" = None
        self._pending_choices: Dict[str, Dict[str, Any]] = {}
        self.effective_mode = self._resolve_mode()
        self._pending_transcriptions: Dict[str, str] = {}

    def _resolve_mode(self) -> str:
        return "polling"

    def send_message(self, chat_id: int | str, text: str, reply_markup: Any = None) -> None:
        if not self._loop or not self.app:
            return

        try:
            chat_ref: int | str = int(chat_id)
        except (TypeError, ValueError):
            chat_ref = chat_id

        async def _send():
            try:
                await self.app.bot.send_message(chat_id=chat_ref, text=text, reply_markup=reply_markup)
            except Exception as exc:
                self.sig_info.emit(f"Envoi message Telegram impossible : {exc}")

        self._loop.call_soon_threadsafe(lambda: asyncio.create_task(_send()))

    def ask_transcription(self, chat_id: int | str, audio_path: str) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        token = secrets.token_urlsafe(8)
        self._pending_transcriptions[token] = audio_path
        name = os.path.basename(audio_path) or audio_path
        buttons = [
            [InlineKeyboardButton("📝 Oui, transcrire", callback_data=f"tr:yes:{token}")],
            [InlineKeyboardButton("⛔ Non", callback_data=f"tr:no:{token}")],
        ]
        self.send_message(chat_id, f"Transcrire l’audio téléchargé ?\n{name}", InlineKeyboardMarkup(buttons))

    def _inspect_url(self, url: str) -> dict:
        u = normalize_yt(url)
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "retries": 2,
            "socket_timeout": 15,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(u, download=False)
        if info and info.get("entries"):
            info = info["entries"][0]
        return info or {}

    def _build_options(self, info: dict) -> Tuple[str, List[Dict[str, Any]]]:
        formats = info.get("formats") or []
        duration = info.get("duration")
        videos = list_video_formats(formats, mp4_friendly=True)
        audio = pick_best_audio(formats, mp4_friendly=True)
        title = info.get("title") or info.get("fulltitle") or info.get("original_url") or "Lien YouTube"
        options: List[Dict[str, Any]] = []
        for vf in videos[:8]:
            vid_id = vf.get("format_id") or ""
            fmt = vid_id
            audio_id = ""
            audio_label = ""
            audio_size = None
            if audio:
                audio_id = audio.get("format_id") or ""
                if audio_id:
                    fmt = f"{vid_id}+{audio_id}"
                audio_label = f"{audio.get('ext','')}/{audio.get('acodec','')}"
                audio_size = estimate_size(audio, duration)
            res = f"{vf.get('height') or ''}p"
            fps = vf.get("fps")
            vc = f"{vf.get('ext','')}/{vf.get('vcodec','')}"
            vsize = estimate_size(vf, duration)
            total = (vsize or 0) + (audio_size or 0)
            parts = [res.strip() or "—", vc]
            if fps:
                parts.insert(1, f"{fps} fps")
            label = " • ".join([p for p in parts if p])
            approx = human_size(total) if total else "—"
            detail = label
            if audio_label:
                detail += f" • Audio {audio_label}"
            detail += f" • ≈ {approx}"
            options.append({
                "fmt": fmt,
                "label": detail,
            })
        return title, options

    async def _cmd_start(self, update, context):
        msg = update.effective_message
        if msg:
            await msg.reply_text("Envoie-moi un lien YouTube pour lancer un téléchargement.")

    async def _handle_text(self, update, context):
        message = update.effective_message
        if not message:
            return
        text = (message.text or "").strip()
        if not text:
            return
        info = None
        try:
            info = await asyncio.get_running_loop().run_in_executor(None, self._inspect_url, text)
        except Exception as exc:
            await message.reply_text(f"Impossible d’inspecter le lien : {exc}")
            return
        if not info:
            await message.reply_text("Impossible d’obtenir les informations de la vidéo.")
            return
        title, options = self._build_options(info)
        if not options:
            await message.reply_text("Aucun format compatible trouvé.")
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        token = secrets.token_urlsafe(8)
        keyboard = [
            [InlineKeyboardButton(opt["label"], callback_data=f"dl:{token}:{idx}")]
            for idx, opt in enumerate(options)
        ]
        self._pending_choices[token] = {
            "url": text,
            "options": options,
            "title": title,
            "chat_id": message.chat_id,
        }
        await message.reply_text(
            f"Formats disponibles pour :\n{title}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def _handle_callback(self, update, context):
        query = update.callback_query
        if not query:
            return
        data = query.data or ""
        chat_id = query.message.chat_id if query.message else None
        if data.startswith("dl:"):
            parts = data.split(":")
            if len(parts) != 3:
                await query.answer("Callback invalide.")
                return
            token, idx_str = parts[1], parts[2]
            entry = self._pending_choices.get(token)
            if not entry:
                await query.answer("Choix expiré.", show_alert=True)
                return
            try:
                idx = int(idx_str)
            except ValueError:
                await query.answer("Choix invalide.")
                return
            options = entry.get("options") or []
            if idx < 0 or idx >= len(options):
                await query.answer("Choix invalide.")
                return
            option = options[idx]
            if chat_id is None:
                await query.answer("Chat introuvable.")
                return
            await query.answer("Téléchargement en cours…", show_alert=False)
            try:
                await query.edit_message_reply_markup(None)
            except Exception:
                pass
            title = entry.get("title") or "Vidéo"
            fmt = option.get("fmt") or ""
            self.sig_download_requested.emit(entry.get("url", ""), fmt, chat_id, title)
            self.send_message(chat_id, f"Format sélectionné : {option.get('label','')}\nTéléchargement demandé…")
            self._pending_choices.pop(token, None)
        elif data.startswith("tr:yes"):
            parts = data.split(":", 2)
            tok = parts[2] if len(parts) == 3 else ""
            audio_path = self._pending_transcriptions.pop(tok, "")
            if not audio_path:
                await query.answer("Lien expiré. Renvoie la vidéo pour réessayer.", show_alert=True)
                return
            await self._handle_transcription_yes(query, chat_id, audio_path)
        elif data.startswith("tr:no"):
            await query.answer("OK", show_alert=False)
            try:
                await query.edit_message_reply_markup(None)
            except Exception:
                pass
            if chat_id is not None:
                self.send_message(chat_id, "Transcription annulée.")
        else:
            await query.answer("Commande inconnue.")

    async def _handle_transcription_yes(self, query, chat_id: Optional[int], audio_path: str):
        if chat_id is None:
            await query.answer("Chat introuvable.")
            return
        webhook_full = (self.app_config.get("webhook_full") or "").strip()
        if not webhook_full:
            await query.answer("Webhook non configuré.", show_alert=True)
            self.send_message(chat_id, "Configure le webhook dans l’app avant de lancer une transcription.")
            return
        await query.answer("Envoi en cours…", show_alert=False)
        loop = asyncio.get_running_loop()
        status, body = await loop.run_in_executor(None, self._post_audio_to_webhook, webhook_full, audio_path)
        try:
            await query.edit_message_reply_markup(None)
        except Exception:
            pass
        if status == 0:
            self.send_message(chat_id, f"Transcription impossible : {body}")
            return
        snippet = body.strip()
        if len(snippet) > 400:
            snippet = snippet[:400] + "\n...[tronqué]..."
        msg = f"Transcription lancée ✅ (HTTP {status})"
        if snippet:
            msg += f"\n{snippet}"
        self.send_message(chat_id, msg)

    def _post_audio_to_webhook(self, url: str, audio_path: str) -> Tuple[int, str]:
        try:
            import requests
        except ImportError:
            return 0, "Le module requests est manquant. Installe-le depuis l’app."
        if not os.path.exists(audio_path):
            return 0, f"Fichier introuvable : {audio_path}"
        mime, _ = mimetypes.guess_type(audio_path)
        mime = mime or "application/octet-stream"
        basename = os.path.basename(audio_path)
        try:
            with open(audio_path, "rb") as handle:
                files = {"data": (basename, handle, mime)}
                resp = requests.post(url, files=files, timeout=(10, 600))
            body = resp.text or ""
            return resp.status_code, body
        except Exception as exc:
            return 0, str(exc)

    def run(self) -> None:
        token = (self.app_config.get("telegram_token") or "").strip()
        if not token:
            self.sig_info.emit("Token Telegram manquant : bot non démarré.")
            return

        if sys.platform.startswith("win"):
            try:
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            except Exception:
                pass

        try:
            self._loop = asyncio.new_event_loop()
        except Exception as exc:
            self.sig_info.emit(f"Erreur bot Telegram : {exc}")
            return

        try:
            asyncio.set_event_loop(self._loop)
            self._stop_evt = asyncio.Event()
            mode = self._resolve_mode()
            base = (self.app_config.get("webhook_base") or "").strip()
            if mode == "webhook" and not base:
                self.sig_info.emit("URL webhook absente, bascule en mode polling.")
                mode = "polling"
            self.effective_mode = mode
            major, minor = _ptb_major_minor()
            self.sig_info.emit(f"python-telegram-bot v{major}.{minor}")

            if mode == "polling":
                self._loop.run_until_complete(self._serve_polling())
            else:
                port = int(self.app_config.get("telegram_port") or 8081)
                self._loop.run_until_complete(self._serve_webhook(base, port))
        except Exception as exc:
            self.sig_info.emit(f"Erreur bot Telegram : {exc}")
        finally:
            if self._loop:
                try:
                    self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                except Exception:
                    pass
                asyncio.set_event_loop(None)
                self._loop.close()
            self._loop = None
            self._stop_evt = None
            self.app = None
            self._pending_choices.clear()
            self.sig_info.emit("Bot Telegram arrêté.")

    async def _build_app(self):
        try:
            from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
        except Exception as exc:
            raise RuntimeError(f"Import python-telegram-bot impossible : {exc}") from exc

        token = (self.app_config.get("telegram_token") or "").strip()
        app = Application.builder().token(token).build()
        self.app = app
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        return app

    async def _serve_polling(self):
        self.sig_info.emit("Bot Telegram en initialisation (polling)…")
        try:
            app = await self._build_app()
        except RuntimeError as exc:
            self.sig_info.emit(str(exc))
            return
        await app.initialize()
        await app.start()
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass

        if app.updater is None:
            self.sig_info.emit("Updater PTB indisponible : polling impossible.")
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass
            return

        try:
            await app.updater.start_polling(drop_pending_updates=False)
        except Exception as exc:
            self.sig_info.emit(f"start_polling a échoué : {exc}")
            try:
                await app.updater.stop()
            except Exception:
                pass
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass
            return

        self.sig_info.emit("Bot Telegram démarré en mode polling.")
        try:
            if self._stop_evt:
                await self._stop_evt.wait()
        finally:
            try:
                await app.updater.stop()
            except Exception:
                pass
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass

    async def _serve_webhook(self, base: str, port: int):
        self.sig_info.emit("Bot Telegram en initialisation (webhook)…")
        try:
            app = await self._build_app()
        except RuntimeError as exc:
            self.sig_info.emit(str(exc))
            return

        await app.initialize()
        await app.start()
        token = (self.app_config.get("telegram_token") or "").strip()
        path = f"tg/{token}"
        base = base.rstrip("/")
        webhook_url = f"{base}/{path}" if base else f"/{path}"

        try:
            from telegram import Update

            await app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
            await app.start_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=path,
                webhook_url=webhook_url,
                drop_pending_updates=True,
            )
        except Exception as exc:
            self.sig_info.emit(f"Impossible de démarrer le webhook : {exc}")
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass
            return

        self.sig_info.emit(f"Webhook : {webhook_url} (port {port})")
        self.sig_info.emit("Bot Telegram démarré en mode webhook.")
        try:
            if self._stop_evt:
                await self._stop_evt.wait()
        finally:
            try:
                await app.stop_webhook()
            except Exception:
                pass
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass

    def stop(self) -> None:
        if self._loop and self._stop_evt:
            self._loop.call_soon_threadsafe(self._stop_evt.set)
