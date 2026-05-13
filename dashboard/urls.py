from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('bugs/<str:external_id>/', views.bug_detail, name='bug_detail'),
    path('presets/', views.presets_data, name='presets_data'),
]
