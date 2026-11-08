import ipaddress
import socket
from urllib.parse import urlparse

from django import forms
from django.conf import settings


class JobForm(forms.Form):
    url = forms.URLField(label="Application URL (login page)")
    username = forms.CharField(label="Login ID / Email")
    password = forms.CharField(label="Password", widget=forms.PasswordInput(render_value=False))
    language = forms.ChoiceField(
        label="Narration language", choices=[("en", "English"), ("hi", "हिन्दी (Hindi)")]
    )
    demo_mode = forms.BooleanField(
        required=False,
        label="Demo mode: enter sample data in forms and save it (for empty test accounts only — "
        "this CREATES records in the app).",
    )
    consent = forms.BooleanField(
        label="I own this application or have permission to access and record it, "
        "and I'm using a test/demo account."
    )

    def clean_url(self):
        url = self.cleaned_data["url"]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise forms.ValidationError("Enter a valid http(s) URL.")
        if not settings.ALLOW_PRIVATE_URLS:
            # SSRF guard: don't let users point our browser at internal services.
            try:
                infos = socket.getaddrinfo(parsed.hostname, None)
            except socket.gaierror:
                raise forms.ValidationError("Could not resolve that host.")
            for info in infos:
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    raise forms.ValidationError("Private/internal addresses are not allowed.")
        return url
