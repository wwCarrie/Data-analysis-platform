from django.urls import path
from . import views

app_name = 'chpa'
urlpatterns = [
    path(r'index', views.index, name="index"),
    path(r'search/<str:column>/<str:kw>', views.search, name='search'),
    path(r'query', views.query, name='query'),
    path(r'export/<str:type>', views.export, name='export'),
    path('logout/', views.logout_view, name='logout'),
]