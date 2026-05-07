# SR860 Impedance Workbench

Aplicación en Python para medir impedancia con un lock-in SRS SR860/SR865, visualizar `R`, `C`, `|Z|` y `L` contra frecuencia, exportar gráficas en `SVG` y controlar el setup del instrumento desde una GUI.

## Funciones principales

- Diagnóstico de conexión con lectura de `*IDN?`, `FREQ?`, `SLVL?` y `SNAP? X,Y`.
- Medición única para validar cableado y respuesta antes de un barrido completo.
- Barrido de impedancia usando resistencia serie conocida.
- Vista automática para resistencias con `R`, `Xz`, `|Z|` y fase contra frecuencia.
- Vista general con `R`, `C`, `|Z|` y `L` contra frecuencia.
- Exportación de CSV, SVG seleccionables y sesión completa en JSON.
- Panel de configuración con scroll para pantallas pequeñas.

## Modelo matemático

La app asume un montaje de divisor serie con resistencia conocida `Rs` y DUT:

```text
Zdut = Rs * Vdut / (Vsource - Vdut)
```

Desde la impedancia compleja `Z = R + jXz`, calcula:

```text
R = real(Z)
|Z| = abs(Z)
Cserie = -1 / (2*pi*f*Xz), sólo si Xz < 0
Lserie = Xz / (2*pi*f), sólo si Xz > 0
```

`C` y `L` son equivalentes serie derivados de la reactancia. Para una resistencia ideal deberían ser inexistentes o poco estables, porque `Xz` se acerca a cero.

## Estado del proyecto

- GUI principal: `barrido.py`
- Icono del proyecto: `srs-1.svg`
- Recursos para la app: `assets/srs-1.png`, `assets/srs-1.ico`
- Build para Windows: carpeta `windows/`

## Ejecución en Linux

```bash
cd ~/SRC_SR860_GUI
./run_gui.sh
```

## Build para Windows

La forma correcta es compilar el `.exe` directamente en una máquina Windows.

### Opción PowerShell

```powershell
cd SR860_Impedance_Workbench
.\windows\build_windows.ps1
```

### Opción CMD

```bat
cd SR860_Impedance_Workbench
windows\build_windows.bat
```

Al terminar, el ejecutable queda en:

```text
dist\SR860_Impedance_Workbench.exe
```

## Instalador para Windows

El repo también incluye un instalador con Inno Setup:

```text
windows/SR860_Impedance_Workbench.iss
```

Después de compilar el `.exe` con PyInstaller, genera el instalador con:

```powershell
iscc .\windows\SR860_Impedance_Workbench.iss
```

Eso produce un instalador en:

```text
dist_installer\SR860_Impedance_Workbench_Setup_v0.1.0.exe
```

## Dependencias de build para Windows

Se instalan automáticamente desde:

```text
windows/requirements-build.txt
```

## Publicación en GitHub

Flujo recomendado:

1. Inicializar el repo local.
2. Hacer el primer commit.
3. Crear el repo remoto en GitHub.
4. Subir por SSH.

Repo publicado:

```text
https://github.com/ROMERUU-dev/sr860-impedance-workbench
```

## Nota sobre conexión USBTMC en Linux

Si el instrumento aparece como `/dev/usbtmc0` pero no conecta, instala la regla `udev` incluida:

```bash
./fix_usbtmc_permissions.sh
```
