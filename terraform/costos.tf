# ---------------------------------------------------------------------------
# Vigilancia de costos
# ---------------------------------------------------------------------------
# Coste de esto: 0 USD. Los presupuestos y las alertas de Azure son gratuitos.
#
# El presupuesto NO corta nada. Azure no apaga recursos por superarlo: solo
# manda un correo. Sirve para enterarse de una desviacion cuando empieza, no al
# recibir la factura a fin de mes.
#
# Va acotado al grupo de recursos del proyecto, no a la suscripcion entera, para
# que el ruido de los otros grupos (rg-sdlcagents, rg-prodbench) no contamine la
# lectura. Esa es justo la ventaja de tenerlo todo en su propio grupo.

resource "azurerm_consumption_budget_resource_group" "mensual" {
  name              = "presupuesto-${local.base}"
  resource_group_id = azurerm_resource_group.principal.id

  amount     = var.presupuesto_mensual_usd
  time_grain = "Monthly"

  time_period {
    start_date = var.presupuesto_inicio
  }

  # NO hay aviso al 50%. Los ~21 USD fijos de PostgreSQL y ACR son el 52% del
  # presupuesto por si solos, asi que ese umbral se cruzaria todos los meses
  # pase lo que pase. Una alerta que siempre suena deja de leerse, y entonces
  # tampoco se lee la que importa.

  # Primer aviso real: 32 USD, por encima del gasto normal (~25-29).
  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = var.emails_alertas_costo
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = var.emails_alertas_costo
  }

  # La mas util de las cuatro: avisa cuando Azure PROYECTA que se va a pasar,
  # segun el ritmo de gasto actual. Llega dias antes de que ocurra, no despues.
  # Es la que detecta el caso tipico: algo dejo de dormir y esta corriendo 24/7.
  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = var.emails_alertas_costo
  }

  lifecycle {
    # Cambiar la fecha de inicio obliga a recrear el presupuesto y se pierde el
    # historial de avisos.
    ignore_changes = [time_period]
  }
}
