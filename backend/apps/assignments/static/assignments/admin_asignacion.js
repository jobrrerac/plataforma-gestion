(function () {
    'use strict';

    function getRow(selector) { return document.querySelector(selector); }

    function aplicarJornadaCompleta() {
        var checkbox = document.getElementById('id_jornada_completa');
        var rowIntensidad = getRow('.field-intensidad_diaria');
        var inputIntensidad = document.getElementById('id_intensidad_diaria');
        if (!checkbox) return;

        var bloqueada = checkbox.checked;
        if (rowIntensidad) rowIntensidad.style.opacity = bloqueada ? '0.35' : '1';
        if (inputIntensidad) {
            inputIntensidad.disabled = bloqueada;
            if (bloqueada) inputIntensidad.value = '';
        }
    }

    function toggleCamposModo() {
        var modoEl = document.getElementById('id_modo_asignacion');
        if (!modoEl) return;
        var modo = modoEl.value;

        var rowHoras     = getRow('.field-horas_totales');
        var rowDias      = getRow('.field-dias_habiles');
        var rowFechaFin  = getRow('.field-fecha_fin_rango');
        var rowJornada   = getRow('.field-jornada_completa');

        if (rowHoras)    rowHoras.style.display    = (modo === 'HORAS') ? '' : 'none';
        if (rowDias)     rowDias.style.display     = (modo === 'DIAS')  ? '' : 'none';
        if (rowFechaFin) rowFechaFin.style.display = (modo === 'RANGO') ? '' : 'none';
        if (rowJornada)  rowJornada.style.display  = (modo === 'RANGO') ? '' : 'none';

        // Al salir del modo RANGO, resetea el checkbox y habilita intensidad
        if (modo !== 'RANGO') {
            var checkbox = document.getElementById('id_jornada_completa');
            if (checkbox) checkbox.checked = false;
            var inputIntensidad = document.getElementById('id_intensidad_diaria');
            if (inputIntensidad) inputIntensidad.disabled = false;
            var rowIntensidad = getRow('.field-intensidad_diaria');
            if (rowIntensidad) rowIntensidad.style.opacity = '1';
        } else {
            aplicarJornadaCompleta();
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        var modoSelect = document.getElementById('id_modo_asignacion');
        if (modoSelect) {
            modoSelect.addEventListener('change', toggleCamposModo);
            toggleCamposModo();
        }

        var jornadaCheck = document.getElementById('id_jornada_completa');
        if (jornadaCheck) {
            jornadaCheck.addEventListener('change', aplicarJornadaCompleta);
        }
    });
})();
