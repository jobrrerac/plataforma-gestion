from rest_framework.routers import DefaultRouter
from .views import AsignacionViewSet

router = DefaultRouter()
router.register("asignaciones", AsignacionViewSet, basename="asignacion")

urlpatterns = router.urls
