from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import FeriadosView, DiaNoLaborableViewSet, IndisponibilidadViewSet

router = DefaultRouter()
router.register("calendario/dias-no-laborables", DiaNoLaborableViewSet, basename="dia-no-laborable")
router.register("calendario/indisponibilidades", IndisponibilidadViewSet, basename="indisponibilidad")

urlpatterns = router.urls + [
    path("calendario/feriados/", FeriadosView.as_view(), name="feriados"),
]
