# fix_akashic_import.py — rode na pasta do projeto: python fix_akashic_import.py
from pathlib import Path
import re

gui_path = Path("gui.py")
if not gui_path.exists():
    raise SystemExit("gui.py nao encontrado nesta pasta.")

text = gui_path.read_text(encoding="utf-8")

if "from pipeline.akashic import build_registro_modelo" in text and "Rode o build" not in text:
    print("Ja esta atualizado.")
    raise SystemExit(0)

new_method = """    def _import_akashic(self):
        path = filedialog.askopenfilename(filetypes=[("Markdown", "*.md *.txt"), ("Todos", "*.*")])
        if not path:
            return
        content = read_file(Path(path))
        write_file(self.project.akashic_path, content)

        from pipeline.akashic import build_registro_modelo
        ok, msg = build_registro_modelo(self.project.project_dir)

        if ok:
            self.akashic_lbl.configure(text="● Registro Akáshico OK", text_color=ACCENT_GREEN)
            messagebox.showinfo("Sucesso", f"Registro Akáshico importado.\n\n{msg}")
        else:
            self.akashic_lbl.configure(text="○ Sem Registro Akáshico", text_color=ACCENT_RED)
            messagebox.showerror("Erro", f"Importado, mas falhou ao gerar o modelo:\n{msg}")
"""

pattern = r"    def _import_akashic\(self\):.*?(?=\n    def |\nclass |\Z)"
m = re.search(pattern, text, re.DOTALL)
if not m:
    raise SystemExit("Metodo _import_akashic nao encontrado no gui.py")

text = text[: m.start()] + new_method + "\n" + text[m.end() :]
gui_path.write_text(text, encoding="utf-8")
print("OK — gui.py corrigido.")
print("Garanta tambem o akashic.py atualizado:")
print("  git checkout origin/main -- pipeline/akashic.py")
print("Depois: python gui.py")
