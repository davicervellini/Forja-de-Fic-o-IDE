"""Imagens do projeto: retratos, capa e o EPUB com capa."""

import base64
import zipfile
from unittest.mock import patch

from pipeline import config, images
from pipeline.export import write_epub, ExportChapter

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def test_nome_seguro_e_caminho_so_dentro_de_imagens(tmp_path):
    assert images.safe_name("R.A.S.P.U.T.I.N..jpeg") == "r_a_s_p_u_t_i_n.jpeg"
    assert images.safe_name("Aelarion Vento-Estelar.JPEG") == "aelarion_vento_estelar.jpeg"
    rel = images.save_image(tmp_path, "Maeve.png", JPEG)
    assert rel == "imagens/maeve.png" and images.resolve(tmp_path, rel).read_bytes() == JPEG
    assert images.save_image(tmp_path, "Maeve.png", JPEG) == "imagens/maeve_2.png"
    (tmp_path / "segredo.png").write_bytes(JPEG)
    assert images.resolve(tmp_path, "../segredo.png") is None
    assert images.resolve(tmp_path, "https://x/y.png") is None
    try:
        images.safe_name("virus.exe")
    except ValueError:
        pass
    else:
        raise AssertionError("aceitou .exe")


def test_epub_com_capa(tmp_path):
    cover = tmp_path / "capa.jpeg"
    cover.write_bytes(JPEG)
    ch = [ExportChapter(num=1, title="Chapter 1: X", scenes=["Hello."])]
    out = write_epub(tmp_path / "b.epub", "Livro", ch, cover=cover)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        opf = z.read("OEBPS/content.opf").decode()
    assert "OEBPS/cover.jpeg" in names and "OEBPS/cover.xhtml" in names
    assert 'properties="cover-image"' in opf and '<itemref idref="cover"/>' in opf


def test_api_envia_imagem_e_define_capa(tmp_path):
    from fastapi.testclient import TestClient
    import webapp.server as srv

    (tmp_path / "projetos").mkdir()
    with patch.object(config, "PROJECTS_DIR", tmp_path / "projetos"), patch.object(config, "DATA_DIR", tmp_path):
        c = TestClient(srv.create_app())
        slug = c.post("/api/projects", json={"name": "Img"}).json()["slug"]
        r = c.post(f"/api/projects/{slug}/media", json={"filename": "Capa.jpeg",
                                                         "data": "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()})
        path = r.json()["path"]
        assert path == "imagens/capa.jpeg"
        got = c.get(f"/api/projects/{slug}/media/capa.jpeg")
        assert got.status_code == 200 and got.content == JPEG
        assert c.get(f"/api/projects/{slug}/media/..%2Fprojeto.json").status_code == 404
        assert c.post(f"/api/projects/{slug}/media", json={"filename": "x.svg", "data": "AAAA"}).status_code == 400
        c.put(f"/api/projects/{slug}/meta", json={"cover": path})
        assert c.get("/api/projects").json()[0]["cover"] == path
