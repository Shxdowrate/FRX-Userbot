"""Совместимость с aiogram 2 для модулей Hikka (в FRX стоит aiogram 3).

aiogram.utils.exceptions (есть только во 2-й версии) → классы ошибок aiogram 3. Подробные исключения 2-й версии
(MessageNotModified, ChatNotFound, …) в 3-й все стали TelegramBadRequest — поэтому `except MessageNotModified`
ловит любую TelegramBadRequest (шире, но модули так и рассчитаны — проглотить ошибку).
"""

from aiogram import exceptions as _e

TelegramAPIError = _e.TelegramAPIError
AIOGramWarning = Warning
TimeoutWarning = Warning

BadRequest = _e.TelegramBadRequest
RetryAfter = _e.TelegramRetryAfter
Unauthorized = _e.TelegramUnauthorizedError
TerminatedByOtherGetUpdates = _e.TelegramConflictError
ConflictError = _e.TelegramConflictError
NetworkError = _e.TelegramNetworkError
NotFound = _e.TelegramNotFound
Throttled = _e.TelegramRetryAfter
ValidationError = _e.TelegramBadRequest
MigrateToChat = _e.TelegramMigrateToChat
ForbiddenError = _e.TelegramForbiddenError
BotBlocked = _e.TelegramForbiddenError
BotKicked = _e.TelegramForbiddenError
UserDeactivated = _e.TelegramForbiddenError
CantInitiateConversation = _e.TelegramForbiddenError
CantTalkWithBots = _e.TelegramForbiddenError

for _name in (
    "MessageNotModified", "MessageToEditNotFound", "MessageToDeleteNotFound", "MessageCantBeDeleted",
    "MessageCantBeEdited", "MessageIdInvalid", "MessageTextIsEmpty", "MessageIsTooLong", "MessageToForwardNotFound",
    "MessageToReplyNotFound", "ChatNotFound", "UserNotFound", "ChatAdminRequired", "NotEnoughRightsToPinMessage",
    "InvalidQueryID", "QueryIsTooOld", "ButtonDataInvalid", "WrongFileIdentifier", "PhotoAsInputFileRequired",
    "BadWebhook", "CantParseEntities", "CantParseUrl", "ResultIdDuplicate", "InlineButtonDataInvalid",
    "MessageNotModifiedError", "ObjectExpectedAsReplyMarkup", "InvalidHTTPUrlContent", "TypeOfFileMismatch",
    "PollError", "MethodNotKnown", "PhotoDimensions", "UnavailableMembers", "TooManyMessagesToDelete",
):
    globals()[_name] = _e.TelegramBadRequest
del _name
