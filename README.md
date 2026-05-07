# SR860 Impedance Workbench

Aplicación en Python para medir impedancia con un lock-in SRS SR860/SR865, visualizar `R`, `C`, `|Z|` y `L` contra frecuencia, exportar gráficas en `SVG` y controlar el setup del instrumento desde una GUI.

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

## Nota sobre conexión USBTMC en Linux

Si el instrumento aparece como `/dev/usbtmc0` pero no conecta, instala la regla `udev` incluida:

```bash
./fix_usbtmc_permissions.sh
```
