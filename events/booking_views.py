from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .booking_services import (
    BookingAccessDenied,
    BookingError,
    BookingExpired,
    BookingNotFound,
    BookingUnavailable,
    InvalidBookingTransition,
    InvalidPricingConfiguration,
    InvalidHoldSelection,
    create_booking_from_holds,
    expire_due_bookings_for_customer,
)
from .models import Booking, Payment
from .payment_providers import PaymentProviderUnavailable
from .payment_services import (
    InvalidIdempotencyKey,
    PaymentRequestConflict,
    initiate_booking_payment,
)
from .refund_services import cancel_booking_with_refund


def _serialize_booking(booking):
    return {
        "id": booking.pk,
        "event_id": booking.event_id,
        "status": booking.status,
        "created_at": booking.created_at.isoformat(),
        "updated_at": booking.updated_at.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "seats": [
            {
                "id": booking_seat.seat_id,
                "row": booking_seat.seat.row_number,
                "number": booking_seat.seat.seat_number,
                "category_id": booking_seat.category_id,
                "category": booking_seat.category_name or None,
                "unit_price": format(booking_seat.unit_price, ".2f"),
            }
            for booking_seat in booking.booking_seats.select_related(
                "seat", "category"
            ).all()
        ],
        "total_price": format(booking.total_price, ".2f"),
        "history": [
            {
                "from_status": entry.from_status or None,
                "to_status": entry.to_status,
                "changed_at": entry.changed_at.isoformat(),
                "actor_id": entry.actor_id,
                "reason": entry.reason,
            }
            for entry in booking.status_history.select_related("actor").all()
        ],
        "payments": [
            _serialize_payment(payment)
            for payment in booking.payments.all()
        ],
    }


def _serialize_payment(payment):
    return {
        "id": payment.pk,
        "provider": payment.provider,
        "provider_reference": payment.provider_reference,
        "amount": format(payment.amount, ".2f"),
        "status": payment.status,
        "created_at": payment.created_at.isoformat(),
        "completed_at": (
            payment.completed_at.isoformat() if payment.completed_at else None
        ),
        "failure_reason": payment.failure_reason,
    }


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_booking(request, event_id):
    hold_ids = request.data.get("hold_ids") if isinstance(request.data, dict) else None
    if not isinstance(hold_ids, list):
        return Response(
            {"detail": "hold_ids must be a list of positive integers."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        booking = create_booking_from_holds(
            event_id=event_id,
            hold_ids=hold_ids,
            customer=request.user,
        )
    except BookingAccessDenied as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    except InvalidHoldSelection as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except InvalidPricingConfiguration as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    except BookingUnavailable as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

    return Response(_serialize_booking(booking), status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def booking_list(request):
    expire_due_bookings_for_customer(customer_id=request.user.pk)
    bookings = (
        Booking.objects.filter(customer=request.user)
        .select_related("event")
        .prefetch_related("booking_seats__seat", "status_history", "payments")
    )
    return Response([_serialize_booking(booking) for booking in bookings])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def booking_detail(request, booking_id):
    get_object_or_404(
        Booking.objects.filter(customer=request.user),
        pk=booking_id,
    )
    expire_due_bookings_for_customer(
        customer_id=request.user.pk,
        booking_ids=[booking_id],
    )
    booking = get_object_or_404(
        Booking.objects.filter(customer=request.user)
        .select_related("event")
        .prefetch_related("booking_seats__seat", "status_history", "payments"),
        pk=booking_id,
    )
    return Response(_serialize_booking(booking))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def start_booking_payment(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.filter(customer=request.user),
        pk=booking_id,
    )
    try:
        payment = initiate_booking_payment(
            booking_id=booking.pk,
            customer=request.user,
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except BookingAccessDenied as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    except BookingExpired as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    except BookingNotFound as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    except InvalidBookingTransition as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    except InvalidIdempotencyKey as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except PaymentRequestConflict as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    except PaymentProviderUnavailable as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except BookingError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    booking = payment.booking
    response_data = _serialize_booking(booking)
    response_data["payment"] = _serialize_payment(payment)
    if payment.status == Payment.Status.PROCESSING:
        response_status = status.HTTP_202_ACCEPTED
    elif payment.status == Payment.Status.FAILED:
        response_status = status.HTTP_402_PAYMENT_REQUIRED
    else:
        response_status = status.HTTP_200_OK
    return Response(response_data, status=response_status)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_booking(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.filter(customer=request.user),
        pk=booking_id,
    )
    if not isinstance(request.data, dict):
        return Response(
            {"detail": "Request body must be an object."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    reason = request.data.get("reason", "")
    if not isinstance(reason, str):
        return Response(
            {"detail": "reason must be a string."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        booking, refund_issued, refund_amount = cancel_booking_with_refund(
            booking_id=booking.pk,
            customer=request.user,
            reason=reason[:2000],
        )
    except BookingAccessDenied as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    except BookingExpired as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    except (BookingNotFound, InvalidBookingTransition) as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    except PaymentProviderUnavailable as exc:
        return Response(
            {"detail": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except BookingError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    response_data = _serialize_booking(booking)
    response_data["refund"] = {
        "issued": refund_issued,
        "amount": format(refund_amount, ".2f"),
    }
    return Response(response_data)
