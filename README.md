# Vitillonario · Dashboard de inversiones

Dashboard en Streamlit que une Trade Republic y MyInvestor: precios casi en tiempo real, mínimos de 3 meses a 5 años con señales de compra, cartera consolidada, aportaciones medias, camino al objetivo y resumen fiscal por ejercicio.

## Publicarlo como web privada (recomendado, sirve para Windows y móvil)

1. Crea un repositorio **privado** en GitHub, por ejemplo `alvaroviti/vitillonario_dashboard`, y sube el contenido de esta carpeta. Desde la web de GitHub vale: *Add file → Upload files*, arrastrando todo.
2. Entra en share.streamlit.io con tu cuenta de GitHub, pulsa *Create app* y elige el repo, la rama `main` y el fichero `app.py`.
3. En *Settings → Secrets* de la app pega el contenido de `.streamlit/secrets.toml.example` con tus valores:
   - `password`: la contraseña para entrar.
   - `github_token`: un token *fine-grained* (GitHub → Settings → Developer settings → Personal access tokens) con acceso solo a este repo y permiso *Contents: Read and write*. Con él, los ficheros que subas desde la app se guardan en el repo y no se pierden al reiniciarse.
   - `github_repo`: `usuario/nombre-del-repo`.
4. En el móvil, abre la URL de la app y usa *Añadir a pantalla de inicio* (Chrome en Android, Safari en iPhone). Se abre como una app más.

Si la app lleva unos días sin uso, Streamlit la duerme y tarda unos segundos en despertar la primera vez.

## Arrancarlo en tu PC (opcional)

Instala Python desde python.org (marca *Add Python to PATH*) y en una terminal, dentro de la carpeta:

```
pip install -r requirements.txt
streamlit run app.py
```

## Actualizar los datos

| Qué | De dónde | Dónde va |
|---|---|---|
| Movimientos Trade Republic | App TR → Perfil → Exportar transacciones (.csv) | `data/tr/` |
| Extracto cuenta MyInvestor | Cuentas → Movimientos → Excel (.xls). Exporta por semestres: más de ~200 filas se cortan | `data/myinvestor/` |
| Foto de posiciones MyInvestor | Inversiones → Posición (fondos, ETFs y plan) | `data/myinvestor/posiciones_AAAA-MM-DD.csv` |

Las exportaciones pueden solaparse: en TR se deduplica por `transaction_id` y en MyInvestor por fecha, concepto, importe y saldo. La pestaña *Datos* comprueba que la cadena de saldos de MyInvestor cuadra y avisa si falta algún tramo.

La foto de posiciones es necesaria porque el extracto de MyInvestor no trae participaciones ni traspasos. Las compras posteriores a la última foto se valoran a coste y aparecen como «pendiente de foto». Conviene actualizarla una vez al mes; el formato es el del fichero de ejemplo.

## Precios y equivalentes

`config/instrumentos.csv` asigna a cada activo un ticker de Yahoo Finance o, si no cotiza en tiempo real (fondos, plan de pensiones), un ETF equivalente (`proxy`). Con el equivalente se estima el valor así: precio de la foto × variación del ETF desde la fecha de la foto.

Las filas con `verificado = revisar` tienen un ticker o equivalente que conviene comprobar. La pestaña *Datos* muestra de dónde sale el precio de cada posición.

## Diversificación

La pestaña *Cartera* reparte el patrimonio por tipo de activo (renta variable, renta fija, monetario y efectivo, oro, cripto) y por región. Cada activo de `config/instrumentos.csv` tiene un `perfil`, y `config/perfiles.csv` dice cómo se reparte ese perfil: por ejemplo, un fondo MSCI World cuenta un 72 % en EE. UU., un 15 % en Europa, etc. Son pesos aproximados del índice: revísalos una vez al año con la ficha del fondo. Un activo nuevo sin perfil aparece como «Sin clasificar».

## Fiscalidad

- Ganancias de Trade Republic por FIFO. Las pérdidas con recompra en ±2 meses se marcan.
- Intereses, dividendos y promociones con sus retenciones. El saveback, las acciones regalo y el staking aparecen aparte como partidas a revisar.
- Aportaciones al plan de pensiones frente al límite de 1.500 €.
- Reembolsos de fondos de MyInvestor: el extracto no trae el coste. Copia la ganancia del informe fiscal en `config/fiscal_manual.csv` (`año,casilla,concepto,importe` con `casilla = ganancia_patrimonial`).
- Hacienda aplica FIFO por valor sumando todos los brokers. El cálculo actual es por plataforma, así que si tienes el mismo ISIN en ambas (ACWI y oro), revisa esas ventas.

Es una estimación para preparar la renta. No sustituye al borrador ni a un asesor.
