/*
 * Datepickers con calendario laboral: pinta en rojo los feriados de Colombia
 * y los días no laborables globales (desde /api/calendario/dias-no-habiles/),
 * y atenúa los fines de semana. Requiere flatpickr + su locale 'es'.
 */
window.inetumCalendario = (function () {
  var noHabiles = {};   // "YYYY-MM-DD" -> nombre del feriado / motivo
  var promesa = null;
  var instancias = [];

  function _iso(d) {
    return d.getFullYear() + '-' +
      String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0');
  }

  function _cargar() {
    if (promesa) return promesa;
    var hoy = new Date();
    var desde = new Date(hoy); desde.setMonth(desde.getMonth() - 6);
    var hasta = new Date(hoy); hasta.setMonth(hasta.getMonth() + 24);
    promesa = fetch(
      '/api/calendario/dias-no-habiles/?desde=' + _iso(desde) + '&hasta=' + _iso(hasta),
      { credentials: 'same-origin', headers: { 'Accept': 'application/json' } }
    )
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (dias) {
        (dias || []).forEach(function (d) { noHabiles[d.fecha] = d.nombre || 'Día no laborable'; });
        instancias.forEach(function (fp) { fp.redraw(); });
      })
      .catch(function () { /* sin datos: el picker sigue funcionando sin colores */ });
    return promesa;
  }

  function _marcarDia(dObj, dStr, fp, dayElem) {
    var d = dayElem.dateObj;
    var clave = _iso(d);
    if (noHabiles[clave]) {
      dayElem.classList.add('dia-no-habil');
      dayElem.title = noHabiles[clave];
    } else if (d.getDay() === 0 || d.getDay() === 6) {
      dayElem.classList.add('dia-finde');
      dayElem.title = 'Fin de semana';
    }
  }

  /* Inicializa flatpickr sobre un input[type=date]. Mantiene el value en
     formato ISO (el backend no cambia) y muestra dd/mm/aaaa al usuario. */
  function init(selector) {
    if (typeof flatpickr === 'undefined') return null;
    var el = document.querySelector(selector);
    if (!el || el._flatpickr) return el ? el._flatpickr : null;
    el.type = 'text'; // evita el picker nativo del navegador
    var fp = flatpickr(el, {
      locale: 'es',
      dateFormat: 'Y-m-d',
      altInput: true,
      altFormat: 'd/m/Y',
      allowInput: true,
      disableMobile: true,
      onDayCreate: _marcarDia,
    });
    instancias.push(fp);
    _cargar();
    return fp;
  }

  return { init: init };
})();
