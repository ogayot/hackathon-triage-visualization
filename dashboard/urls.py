from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('bugs/<str:external_id>/', views.bug_detail, name='bug_detail'),
    path('presets/', views.presets_data, name='presets_data'),
    path('api/run/<str:operation_name>/', views.run_operation, name='run_operation'),
    path('api/status/', views.operation_status, name='operation_status'),
    path('api/presets/', views.manage_presets, name='manage_presets'),
    path('api/presets/<int:preset_id>/', views.manage_presets, name='manage_preset'),
]
