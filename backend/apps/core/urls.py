from rest_framework.routers import DefaultRouter
from .views import RecursoViewSet, ProyectoViewSet

router = DefaultRouter()
router.register("recursos", RecursoViewSet, basename="recurso")
router.register("proyectos", ProyectoViewSet, basename="proyecto")

urlpatterns = router.urls
