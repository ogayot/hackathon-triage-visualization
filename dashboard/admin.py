from django.contrib import admin
from .models import Bug, BugSource

admin.site.register(Bug)
admin.site.register(BugSource)
