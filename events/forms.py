from django import forms

from .models import Event, EventCategory


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        exclude = ("organizer", "created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select")
            else:
                field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get("pricing_strategy") == Event.PricingStrategy.EARLY_BIRD
            and self.instance.pk
        ):
            categories = self.instance.categories.all()
            default_category_id = categories.order_by("pk").values_list(
                "pk", flat=True
            ).first()
            for category in categories:
                configured_early_price = (
                    cleaned_data.get("early_bird_price")
                    if category.pk == default_category_id
                    else category.early_bird_price
                )
                if configured_early_price is None:
                    self.add_error(
                        None,
                        f"Add an early-bird price to the {category.name} category "
                        "before enabling early-bird pricing.",
                    )
        return cleaned_data

    def save(self, commit=True):
        event = super().save(commit=commit)
        if commit and {
            "pricing_strategy",
            "base_price",
            "early_bird_price",
            "early_bird_starts_at",
            "early_bird_ends_at",
        }.intersection(self.changed_data):
            category = event.categories.order_by("pk").first()
            if category is not None:
                category.base_price = event.base_price
                category.early_bird_price = event.early_bird_price
                category.save()
        return event


class EventCategoryForm(forms.ModelForm):
    class Meta:
        model = EventCategory
        fields = ("name", "base_price", "early_bird_price")

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.instance.event = event
        self.fields["early_bird_price"].required = (
            event.pricing_strategy == Event.PricingStrategy.EARLY_BIRD
        )
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select")
            else:
                field.widget.attrs.setdefault("class", "form-control")

    def save(self, commit=True):
        category = super().save(commit=False)
        category.event = self.event
        if commit:
            category.save()
        return category
