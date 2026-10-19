from django import forms


class FormulaireStyle:
    """Applique le style des maquettes aux champs (classes Tailwind `champ`)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for champ in self.fields.values():
            widget = champ.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "h-5 w-5")
            elif isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("class", "champ h-auto py-2.5")
                widget.attrs.setdefault("rows", 3)
            else:
                widget.attrs.setdefault("class", "champ")
