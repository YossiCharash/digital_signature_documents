"""Inbound provider webhooks (currently Amazon SES feedback via SNS)."""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.services.sns_service import SNSVerificationError, handle_sns_message
from app.utils.logger import logger

webhook_router = APIRouter(tags=["webhooks"])


@webhook_router.post("/webhooks/ses")
async def ses_feedback(request: Request) -> JSONResponse:
    """Receive SES bounce/complaint notifications from Amazon SNS.

    Point an SNS subscription (fed by an SES Configuration Set event
    destination, or the identity's feedback notifications) at this URL. The
    first message is a SubscriptionConfirmation, which is auto-confirmed;
    subsequent bounce/complaint events add the address to the suppression list.

    SNS posts with Content-Type text/plain, so the body is read raw rather than
    parsed as JSON by FastAPI.
    """
    raw = await request.body()
    try:
        result = await handle_sns_message(raw)
    except SNSVerificationError as e:
        # Reject forged / malformed messages. SNS will retry a few times; a
        # persistently invalid message is eventually dropped, which is fine.
        logger.warning("Rejected SNS message: %s", e)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST, content={"error": str(e)}
        )
    except Exception as e:
        # Unexpected error: 500 so SNS retries rather than dropping the event.
        logger.error("Error handling SNS message: %s", e, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal error"},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content=result)
