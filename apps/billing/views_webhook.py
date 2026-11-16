from __future__ import annotations

import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.exceptions import DomainError, PermissionFailure, ValidationFailure

from . import services

logger = logging.getLogger("ba.billing")


@csrf_exempt
@require_POST
def paystack_webhook(request):
    """Signature-verified, idempotent provider callback.

    The browser return page never marks a task paid; only this endpoint and
    finance verification can release work.
    """
    signature = request.headers.get("X-Paystack-Signature", "")
    try:
        event, duplicate = services.process_webhook(
            raw_body=request.body, signature=signature
        )
    except PermissionFailure:
        logger.warning("Rejected paystack webhook with an invalid signature")
        return JsonResponse({"detail": "Invalid signature."}, status=401)
    except ValidationFailure as exc:
        logger.warning("Rejected malformed paystack webhook: %s", exc.message)
        return JsonResponse({"detail": "Malformed payload."}, status=400)
    except services.PaymentProviderUnavailable:
        # Nothing was stored; Paystack retries the event later.
        logger.warning("Paystack verify call failed; asking Paystack to retry the webhook")
        return JsonResponse({"detail": "Try again later."}, status=503)
    except RuntimeError:
        logger.error("Paystack webhook received while payments are disabled")
        return JsonResponse({"detail": "Payments are not enabled."}, status=503)
    except DomainError:
        return JsonResponse({"detail": "Webhook rejected."}, status=400)

    return JsonResponse(
        {
            "status": "ok",
            "duplicate": duplicate,
            "outcome": event.outcome or "pending",
        }
    )


@csrf_exempt
@require_POST
def flutterwave_webhook(request):
    """Flutterwave's callback for US dollar payments. Same rules as Paystack's:
    the secret hash must match, each event is stored once, and Flutterwave's own
    verify answer must agree with our record before anything is marked paid."""
    signature = request.headers.get("verif-hash", "")
    try:
        event, duplicate = services.process_webhook(
            raw_body=request.body, signature=signature, provider="flutterwave"
        )
    except PermissionFailure:
        logger.warning("Rejected flutterwave webhook with an invalid hash")
        return JsonResponse({"detail": "Invalid signature."}, status=401)
    except ValidationFailure as exc:
        logger.warning("Rejected malformed flutterwave webhook: %s", exc.message)
        return JsonResponse({"detail": "Malformed payload."}, status=400)
    except services.PaymentProviderUnavailable:
        logger.warning("Flutterwave verify call failed; asking Flutterwave to retry the webhook")
        return JsonResponse({"detail": "Try again later."}, status=503)
    except RuntimeError:
        logger.error("Flutterwave webhook received while payments are disabled")
        return JsonResponse({"detail": "Payments are not enabled."}, status=503)
    except DomainError:
        return JsonResponse({"detail": "Webhook rejected."}, status=400)
    return JsonResponse({"status": "ok", "duplicate": duplicate, "outcome": event.outcome or "pending"})
