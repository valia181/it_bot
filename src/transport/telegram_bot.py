"""Telegram transport adapter for the decoupled IT support core.

Implements TKT-011 | REQ-F-001, REQ-F-002, REQ-F-012, REQ-NF-006,
REQ-NF-008.
"""

from io import BytesIO
import logging
import os
from pathlib import Path, PurePath
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from src.core.models import ApiErrorCode, QueryClassification, SessionCommand
from src.core.orchestrator import ChatOrchestrator
from src.core.session_manager import SessionManager


NON_TEXT_PAYLOAD_MESSAGE = (
    "Наразі підтримуються лише текстові повідомлення. Будь ласка, опишіть "
    "ваше ІТ-питання текстом."
)
INVALID_TEXT_MESSAGE = (
    "Введіть текстове ІТ-питання довжиною від 1 до 4000 символів."
)
MAX_TEXT_LENGTH = 4000
logger = logging.getLogger(__name__)


class TelegramBotConfigurationError(RuntimeError):
    """Raised when the live Telegram bot cannot be configured safely."""


async def handle_telegram_error(
    _update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Log Telegram errors and stop polling when another getUpdates client conflicts."""
    error = context.error
    if isinstance(error, Conflict):
        logger.error(
            "Telegram polling conflict: another client is already calling getUpdates. "
            "Stopping this bot; ensure only one poller uses this token."
        )
        context.application.stop_running()
        return

    if error is None:
        logger.error("Unhandled Telegram application error without exception details.")
        return
    logger.error("Unhandled Telegram application error.", exc_info=error)


def load_telegram_bot_token() -> str:
    """Load and validate TELEGRAM_BOT_TOKEN from the environment or .env file."""
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise TelegramBotConfigurationError(
            "TELEGRAM_BOT_TOKEN is required to start the Telegram bot."
        )
    return token


class TelegramBotAdapter:
    """Map Telegram updates and outbound calls onto the core services.

    Implements TKT-011 | REQ-F-001, REQ-F-002, REQ-F-012, REQ-NF-006,
    REQ-NF-008.
    """

    def __init__(
        self,
        session_manager: Optional[SessionManager] = None,
        orchestrator: Optional[ChatOrchestrator] = None,
    ):
        self.orchestrator = orchestrator or ChatOrchestrator(
            session_manager=session_manager
        )
        self.session_manager = session_manager or self.orchestrator.session_manager

    @staticmethod
    def _chat_id(update: Update) -> int:
        chat = update.effective_chat
        if chat is None:
            raise ValueError("Telegram update does not contain a chat.")
        return chat.id

    async def handle_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Reset the sender's history and send the Ukrainian welcome (TKT-011, REQ-F-001)."""
        chat_id = self._chat_id(update)
        response = self.session_manager.initialize_session(
            str(chat_id), command=SessionCommand.START
        )
        await context.bot.send_message(
            chat_id=chat_id, text=response.welcome_message
        )

    async def handle_reset(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Reset the sender's history and send the welcome (TKT-011, REQ-F-001)."""
        chat_id = self._chat_id(update)
        response = self.session_manager.initialize_session(
            str(chat_id), command=SessionCommand.RESET
        )
        await context.bot.send_message(
            chat_id=chat_id, text=response.welcome_message
        )

    async def handle_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Send usage guidance without mutating state (TKT-011, REQ-F-001, REQ-NF-006)."""
        chat_id = self._chat_id(update)
        await context.bot.send_message(
            chat_id=chat_id, text=self.session_manager.get_help_message()
        )

    @staticmethod
    def _payload_error_code(message) -> Optional[ApiErrorCode]:
        attachment_fields = ("photo", "sticker", "voice", "video", "audio", "document")
        if any(getattr(message, field, None) for field in attachment_fields):
            return ApiErrorCode.NON_TEXT_PAYLOAD_UNSUPPORTED
        text = getattr(message, "text", None)
        if not isinstance(text, str):
            return ApiErrorCode.NON_TEXT_PAYLOAD_UNSUPPORTED
        if not text.strip() or len(text) > MAX_TEXT_LENGTH:
            return ApiErrorCode.VALIDATION_ERROR
        return None

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Validate payloads and route text through the real core (TKT-011, REQ-F-002, REQ-F-012)."""
        chat_id = self._chat_id(update)
        message = update.effective_message
        if message is None:
            raise ValueError("Telegram update does not contain a message.")

        payload_error = self._payload_error_code(message)
        if payload_error is not None:
            rejection_text = (
                NON_TEXT_PAYLOAD_MESSAGE
                if payload_error == ApiErrorCode.NON_TEXT_PAYLOAD_UNSUPPORTED
                else INVALID_TEXT_MESSAGE
            )
            await context.bot.send_message(
                chat_id=chat_id, text=rejection_text
            )
            return

        text = message.text
        if not isinstance(text, str):
            raise RuntimeError("Payload validator accepted a non-text message.")
        response = self.orchestrator.process_message(str(chat_id), text)
        await context.bot.send_message(chat_id=chat_id, text=response.reply_text)
        if (
            response.classification == QueryClassification.API_FALLBACK_DOCUMENTS
            and response.attachments
        ):
            for attachment in response.attachments:
                filename = PurePath(attachment.document_id).name
                if (
                    filename != attachment.document_id
                    or PurePath(filename).suffix.lower() not in {".md", ".txt"}
                ):
                    raise ValueError(
                        f"Unsupported knowledge-base attachment: {attachment.document_id}"
                    )
                document = BytesIO(attachment.content.encode("utf-8"))
                document.name = filename
                await context.bot.send_document(
                    chat_id=chat_id, document=document, filename=filename
                )


def build_telegram_application(
    adapter: TelegramBotAdapter, token: Optional[str] = None
) -> Application:
    """Build the polling application with the TKT-011 transport handlers."""
    resolved_token = token if token is not None else load_telegram_bot_token()
    if not resolved_token or not resolved_token.strip():
        raise TelegramBotConfigurationError(
            "TELEGRAM_BOT_TOKEN must not be empty to start the Telegram bot."
        )

    application = Application.builder().token(resolved_token.strip()).build()
    application.add_handler(CommandHandler("start", adapter.handle_start))
    application.add_handler(CommandHandler("reset", adapter.handle_reset))
    application.add_handler(CommandHandler("help", adapter.handle_help))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, adapter.handle_message)
    )
    application.add_error_handler(handle_telegram_error)
    return application


def run_telegram_bot(token: Optional[str] = None) -> None:
    """Start the live Telegram polling runner after validating configuration."""
    resolved_token = token if token is not None else load_telegram_bot_token()
    if not resolved_token or not resolved_token.strip():
        raise TelegramBotConfigurationError(
            "TELEGRAM_BOT_TOKEN must not be empty to start the Telegram bot."
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    build_telegram_application(
        TelegramBotAdapter(), token=resolved_token
    ).run_polling()


if __name__ == "__main__":
    run_telegram_bot()
