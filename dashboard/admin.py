from django.contrib import admin
from .models import Bug, BugSource, Preset

admin.site.register(Bug)
admin.site.register(BugSource)
admin.site.register(Preset)
