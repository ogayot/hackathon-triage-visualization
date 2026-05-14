from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('status/', views.status_page, name='status_page'),
    path('bugs/<path:external_id>/', views.bug_detail, name='bug_detail'),
    path('presets/', views.presets_data, name='presets_data'),
    path('api/run/<str:operation_name>/', views.run_operation, name='run_operation'),
    path('api/status/', views.operation_status, name='operation_status'),
    path('api/cancel/', views.cancel_operation, name='cancel_operation'),
    path('api/presets/', views.manage_presets, name='manage_presets'),
    path('api/presets/<int:preset_id>/', views.manage_presets, name='manage_preset'),
    path('api/presets/<int:preset_id>/views/', views.manage_views, name='manage_views'),
    path('api/presets/<int:preset_id>/views/<int:view_id>/', views.manage_views, name='manage_view'),
    path("correlations/", views.correlations_json, name="correlations_json"),
    path("bugs/<path:external_id>/correlated/", views.correlated_bugs, name="correlated_bugs"),
]
