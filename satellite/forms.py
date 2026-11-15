from datetime import date

from django import forms

from .services.tasks import FAMILIES

FAMILY_CHOICES = [("MODIS", "MODIS Terra + Aqua"), ("VIIRS_SNPP", "VIIRS Suomi-NPP"),
                  ("VIIRS_NOAA20", "VIIRS NOAA-20"), ("VIIRS_NOAA21", "VIIRS NOAA-21")]
MAX_UI_DAYS = 366


class ImportForm(forms.Form):
    families = forms.MultipleChoiceField(choices=FAMILY_CHOICES, widget=forms.CheckboxSelectMultiple,
                                         initial=["MODIS", "VIIRS_SNPP"], label="Satellites")
    south = forms.FloatField(min_value=-90, max_value=90)
    west = forms.FloatField(min_value=-180, max_value=180)
    north = forms.FloatField(min_value=-90, max_value=90)
    east = forms.FloatField(min_value=-180, max_value=180)
    start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        d = super().clean()
        if self.errors:
            return d
        if not d["south"] < d["north"]:
            raise forms.ValidationError("South must be smaller than north.")
        if not d["west"] < d["east"]:
            raise forms.ValidationError("West must be smaller than east (areas crossing the antimeridian are not supported).")
        if d["start"] > d["end"]:
            raise forms.ValidationError("The start date must not be after the end date.")
        if d["end"] > date.today():
            raise forms.ValidationError("The end date cannot be in the future.")
        if (d["end"] - d["start"]).days + 1 > MAX_UI_DAYS:
            raise forms.ValidationError(f"The web form imports at most {MAX_UI_DAYS} days at a time "
                                        "(use the import_firms command for longer periods).")
        return d


class HarmonizeForm(forms.Form):
    scope = forms.ChoiceField(choices=[("real", "NASA observations"), ("demo", "Synthetic demonstration data")])
    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
