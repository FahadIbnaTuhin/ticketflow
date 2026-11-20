from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods, require_POST
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import role_required

from .forms import EventForm
from .forms import EventCategoryForm
from .models import Event, EventCategory, Seat
from .pricing import calculate_seat_price
from .services import (
    CustomerRequired,
    InvalidSeatSelection,
    SeatUnavailable,
    create_seat_holds,
)


def event_list(request):
    events = Event.objects.select_related("organizer").all()
    search_query = request.GET.get("q", "").strip()
    selected_city = request.GET.get("city", "").strip()
    selected_date = request.GET.get("date", "").strip()

    if search_query:
        events = events.filter(name__icontains=search_query)
    if selected_city:
        events = events.filter(city__iexact=selected_city)
    if selected_date:
        try:
            event_date = parse_date(selected_date)
        except ValueError:
            event_date = None
        if event_date is not None:
            events = events.filter(event_date=event_date)

    paginator = Paginator(events, 10)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "events/event_list.html",
        {
            "events": page_obj.object_list,
            "page_obj": page_obj,
            "cities": Event.objects.order_by("city")
            .values_list("city", flat=True)
            .distinct(),
            "search_query": search_query,
            "selected_city": selected_city,
            "selected_date": selected_date,
            "query_string": query_params.urlencode(),
        },
    )


def event_detail(request, pk):
    event = get_object_or_404(Event.objects.select_related("organizer"), pk=pk)
    seat_rows = {}
    for seat in event.seats.select_related("category").all():
        try:
            seat.current_price = (
                calculate_seat_price(event, category=seat.category)
                if seat.category_id
                else None
            )
        except ValidationError:
            seat.current_price = None
        seat_rows.setdefault(seat.row_number, []).append(seat)
    return render(
        request,
        "events/event_detail.html",
        {"event": event, "seat_rows": seat_rows.items()},
    )


@role_required("organizer")
def organizer_event_list(request):
    events = Event.objects.filter(organizer=request.user)
    return render(request, "events/organizer_event_list.html", {"events": events})


@role_required("organizer")
@require_http_methods(["GET", "POST"])
def event_create(request):
    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.organizer = request.user
            event.save()
            messages.success(request, "Event created.")
            return redirect("events:organizer-list")
    else:
        form = EventForm()
    return render(
        request,
        "events/event_form.html",
        {"form": form, "page_title": "Create event"},
    )


@role_required("organizer")
@require_http_methods(["GET", "POST"])
def event_update(request, pk):
    event = get_object_or_404(Event, pk=pk, organizer=request.user)
    if request.method == "POST":
        form = EventForm(request.POST, instance=event)
        if form.is_valid():
            form.save()
            messages.success(request, "Event updated.")
            return redirect("events:organizer-list")
    else:
        form = EventForm(instance=event)
    return render(
        request,
        "events/event_form.html",
        {"form": form, "event": event, "page_title": "Edit event"},
    )


@role_required("organizer")
@require_POST
def event_delete(request, pk):
    event = get_object_or_404(Event, pk=pk, organizer=request.user)
    event.delete()
    messages.success(request, "Event deleted.")
    return redirect("events:organizer-list")


@role_required("organizer")
def event_category_list(request, pk):
    event = get_object_or_404(Event, pk=pk, organizer=request.user)
    form = EventCategoryForm(event=event)
    return render(
        request,
        "events/event_categories.html",
        {
            "event": event,
            "form": form,
            "categories": event.categories.all(),
            "seats": event.seats.select_related("category").all(),
        },
    )


@role_required("organizer")
@require_POST
def event_category_create(request, pk):
    event = get_object_or_404(Event, pk=pk, organizer=request.user)
    form = EventCategoryForm(request.POST, event=event)
    if form.is_valid():
        form.save()
        messages.success(request, "Seat category created.")
        return redirect("events:category-list", pk=event.pk)
    return render(
        request,
        "events/event_categories.html",
        {
            "event": event,
            "form": form,
            "categories": event.categories.all(),
            "seats": event.seats.select_related("category").all(),
        },
    )


@role_required("organizer")
@require_http_methods(["GET", "POST"])
def event_category_update(request, pk, category_id):
    event = get_object_or_404(Event, pk=pk, organizer=request.user)
    category = get_object_or_404(EventCategory, pk=category_id, event=event)
    form = EventCategoryForm(
        request.POST or None,
        instance=category,
        event=event,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Seat category updated.")
        return redirect("events:category-list", pk=event.pk)
    return render(
        request,
        "events/event_category_form.html",
        {"event": event, "category": category, "form": form},
    )


@role_required("organizer")
@require_POST
def event_category_delete(request, pk, category_id):
    event = get_object_or_404(Event, pk=pk, organizer=request.user)
    category = get_object_or_404(EventCategory, pk=category_id, event=event)
    if category.seats.exists() or category.booking_seats.exists():
        messages.error(
            request,
            "Reassign its seats before deleting a category used by seats or bookings.",
        )
    else:
        category.delete()
        messages.success(request, "Seat category deleted.")
    return redirect("events:category-list", pk=event.pk)


@role_required("organizer")
@require_POST
def assign_seat_category(request, pk, seat_id):
    event = get_object_or_404(Event, pk=pk, organizer=request.user)
    seat = get_object_or_404(Seat, pk=seat_id, event=event)
    try:
        category_id = int(request.POST.get("category_id", ""))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Choose a valid category for this event.")
    if category_id < 1:
        return HttpResponseBadRequest("Choose a valid category for this event.")
    category = get_object_or_404(
        EventCategory,
        pk=category_id,
        event=event,
    )
    seat.category = category
    seat.save(update_fields=("category",))
    messages.success(
        request,
        f"Category updated for row {seat.row_number}, seat {seat.seat_number}.",
    )
    return redirect("events:category-list", pk=event.pk)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_event_seat_holds(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    seat_ids = request.data.get("seat_ids") if isinstance(request.data, dict) else None
    if not isinstance(seat_ids, list):
        return Response(
            {"detail": "seat_ids must be a list of positive integers."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        holds = create_seat_holds(
            event_id=event.pk,
            seat_ids=seat_ids,
            customer=request.user,
        )
    except CustomerRequired as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    except InvalidSeatSelection as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except SeatUnavailable as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

    return Response(
        {
            "holds": [
                {
                    "id": hold.pk,
                    "seat_id": hold.seat_id,
                    "expires_at": hold.expires_at.isoformat(),
                }
                for hold in holds
            ]
        },
        status=status.HTTP_201_CREATED,
    )
