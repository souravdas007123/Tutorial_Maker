from django.urls import path

from . import views

urlpatterns = [
    path("", views.job_list, name="job_list"),
    path("new/", views.job_new, name="job_new"),
    path("job/<uuid:pk>/", views.job_detail, name="job_detail"),
    path("job/<uuid:pk>/approve/", views.job_approve, name="job_approve"),
]
