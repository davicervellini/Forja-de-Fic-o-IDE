import os
import subprocess
import customtkinter

# Descobre o caminho onde o customtkinter está instalado
ctk_path = os.path.dirname(customtkinter.__file__)

# Comando do PyInstaller
# --noconfirm: Sobrescreve a pasta 'dist' se já existir
# --onedir: Cria uma pasta com o executável (mais rápido para abrir do que --onefile)
# --windowed: Oculta a janela do console (CMD)
# --add-data: Copia os arquivos essenciais de tema do customtkinter
# --name: Nome do executável final
command = [
    "pyinstaller",
    "--noconfirm",
    "--windowed",
    "--name", "Forja de Ficcao IDE",
    f"--add-data={ctk_path};customtkinter/",
    "main.py"
]

print("Iniciando construção do executável...")
print("Comando:", " ".join(command))
print("-" * 50)

subprocess.run(command)

print("-" * 50)
print("Construção concluída! O executável está na pasta 'dist/Forja de Ficcao IDE'.")
