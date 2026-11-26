from django.urls import path

from .views import AuthReceiver, Login, Logout

urlpatterns = [
    path('auth-receiver/', AuthReceiver.as_view(), name='auth_receiver'),
    path('login/', Login.as_view(), name='login'),
    path('logout/', Logout.as_view(), name='logout'),
]
