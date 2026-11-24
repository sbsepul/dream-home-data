import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("collect_school_fees", ROOT / "scripts" / "collect_school_fees.py")
fees = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = fees
SPEC.loader.exec_module(fees)

UF = 40_000.0


def mention_tuples(text):
    return [(m.categoria, m.monto_clp, m.unidad) for m in fees.find_mentions(text, "https://c.cl", "html", UF)]


class NameTests(unittest.TestCase):
    def test_domain_candidates_drop_generic_words(self):
        candidates = fees.domain_candidates("COLEGIO SAN IGNACIO EL BOSQUE")
        self.assertIn("colegiosanignaciobosque.cl", candidates)
        self.assertIn("sanignacio.cl", candidates)
        self.assertTrue(all(candidate.endswith(".cl") for candidate in candidates))

    def test_extended_candidates_add_comuna_and_com_without_repeating(self):
        basic = fees.domain_candidates("COLEGIO SAN IGNACIO")
        extended = fees.domain_candidates("COLEGIO SAN IGNACIO", "CONCEPCIÓN", extended=True)
        self.assertIn("sanignacioconcepcion.cl", extended)
        self.assertIn("sanignacio.com", extended)
        self.assertFalse(set(basic) & set(extended))

    def test_distinctive_tokens_ignore_saint_words(self):
        self.assertEqual(fees.distinctive_tokens("COLEGIO SAN IGNACIO EL BOSQUE"), {"IGNACIO", "BOSQUE"})
        self.assertEqual(fees.distinctive_tokens("COLEGIO SANTA MARIA"), {"SANTA", "MARIA"})

    def test_homepage_mentions_measure_name_presence(self):
        self.assertEqual(fees.homepage_mentions("Bienvenidos al Colegio The Grange", "Inicio", "THE GRANGE SCHOOL"), 1.0)
        self.assertEqual(fees.homepage_mentions("Otro colegio", "", "THE GRANGE SCHOOL"), 0.0)

    def test_osm_site_ignores_free_mail(self):
        self.assertEqual(fees.osm_site({"contact:email": "admision@colegiox.cl"}), ("https://colegiox.cl", "osm_email_domain"))
        self.assertEqual(fees.osm_site({"email": "colegio@gmail.com"}), (None, None))
        self.assertEqual(fees.osm_site({"website": "www.colegio.cl"}), ("https://www.colegio.cl", "osm_website"))


class MentionTests(unittest.TestCase):
    def test_pesos_uf_and_bare_amounts(self):
        found = mention_tuples("Matrícula 2026: $ 450.000. Colegiatura mensual UF 12,5. Cuota de incorporación 60 UF. Arancel anual 4.500.000")
        self.assertEqual(found, [("matricula", 450_000, "clp"), ("mensualidad", 500_000, "uf"), ("incorporacion", 2_400_000, "uf"), ("arancel_anual", 4_500_000, "clp")])

    def test_rut_phone_and_far_amounts_are_ignored(self):
        self.assertEqual(mention_tuples("Matrícula a nombre de RUT 76.123.456-7"), [])
        self.assertEqual(mention_tuples("Matrícula " + "x" * 200 + " $ 450.000"), [])

    def test_implausible_amounts_are_dropped(self):
        self.assertEqual(mention_tuples("Mensualidad $ 1.000"), [])
        self.assertEqual(mention_tuples("Matrícula $ 90.000.000"), [])

    def test_label_before_wins_over_next_heading(self):
        found = mention_tuples("COLEGIATURA MENSUAL 2026 Prekínder y Kínder: $ 617.000 1° básico a IV° medio: $ 780.000 CUOTA DE INCORPORACIÓN 2026")
        self.assertEqual(found[-1], ("mensualidad", 780_000, "clp"))

    def test_adjacent_label_after_amount(self):
        self.assertEqual(mention_tuples("Valores 2026 ................................ $ 480.000 matrícula estudiantes nuevos"), [("matricula", 480_000, "clp")])

    def test_period_after_amount_overrides_label(self):
        found = mention_tuples("Matrícula UF 13. Colegiatura 2027 UF 108 anual o UF 10,8 x 10")
        self.assertEqual(found, [("matricula", 520_000, "uf"), ("arancel_anual", 4_320_000, "uf"), ("mensualidad", 432_000, "uf")])

    def test_contract_debts_are_not_monthly_fees(self):
        self.assertEqual(mention_tuples("adeuda por mensualidades la cantidad de $2.150.000"), [])

    def test_admission_fee_is_application_not_monthly(self):
        self.assertEqual(mention_tuples("Mensualidad según nivel. Cuota Admisión $40.000 pago único"), [("postulacion", 40_000, "clp")])

    def test_other_charges_near_fee_words_are_skipped(self):
        self.assertEqual(mention_tuples("Colegiatura mensual. Cuota Centro de Padres 2025: $35.000 por familia"), [])
        self.assertEqual(mention_tuples("Beca: el ingreso declarado per cápita mensual debe ser inferior a $352.743"), [])
        self.assertNotIn("arancel_anual", [item[0] for item in mention_tuples("La matrícula es de 16,72 UF, con un tope de 66,88 UF anual por familia")])

    def test_year_comes_from_nearby_text(self):
        found = fees.find_mentions("Aranceles 2027. Mensualidad $ 380.000", "u", "pdf", UF)
        self.assertEqual(found[0].anio, 2027)
        found = fees.find_mentions("Colegiatura 2026 $ 380.000. Postulación 2028 abierta", "u", "html", UF)
        self.assertEqual(found[0].anio, 2026)


class SummaryTests(unittest.TestCase):
    def test_latest_year_wins_and_range_by_level(self):
        mentions = [
            fees.Mention("mensualidad", 300_000, "clp", 300_000, 2025, "a", "html", ""),
            fees.Mention("mensualidad", 350_000, "clp", 350_000, 2026, "b", "pdf", ""),
            fees.Mention("mensualidad", 420_000, "clp", 420_000, 2026, "b", "pdf", ""),
        ]
        summary = fees.summarize_school(mentions)["mensualidad"]
        self.assertEqual((summary["min_clp"], summary["max_clp"], summary["anio"], summary["tipo_fuente"]), (350_000, 420_000, 2026, "pdf"))

    def test_outliers_from_other_columns_are_dropped(self):
        self.assertEqual(fees.robust_values([298_000, 372_000, 410_000, 1_117_000]), [298_000, 372_000, 410_000])
        self.assertEqual(fees.robust_values([300_000, 1_100_000]), [300_000, 1_100_000])

    def test_extra_services_are_not_tuition(self):
        self.assertEqual(mention_tuples("Valores After School 2027: $150.000 mensuales"), [])
        self.assertEqual(mention_tuples("Jornada Extendida: adicional a la mensualidad, 26,38 UF anuales"), [])

    def test_annual_fee_derives_monthly(self):
        summary = fees.summarize_school([fees.Mention("arancel_anual", 4_000_000, "clp", 4_000_000, 2026, "a", "html", "")])
        self.assertEqual(summary["mensualidad_derivada"]["max_clp"], 400_000)


class ConfidenceTests(unittest.TestCase):
    def test_comuna_must_appear_for_guessed_domains(self):
        self.assertTrue(fees.mentions_comuna(["Dirección: Av. Pedro de Valdivia 123, Ñuñoa"], "ÑUÑOA"))
        self.assertFalse(fees.mentions_comuna(["Santiago Centro"], "CONCEPCIÓN"))
        self.assertEqual(fees.site_confidence({"metodo": "dominio_candidato"}, False), "baja")
        self.assertEqual(fees.site_confidence({"metodo": "dominio_candidato"}, True), "alta")
        self.assertEqual(fees.site_confidence({"metodo": "osm_website", "coincidencia": 1.0}, False), "alta")

    def test_coverage_counts_only_trusted_sites(self):
        monthly = {"mensualidad": {"min_clp": 1, "max_clp": 2, "anio": 2026, "tipo_fuente": "pdf"}}
        data = {"colegios": [
            {"rbd": 1, "sitio": "https://a.cl", "confianza_sitio": "alta", "paginas": 3, "montos": monthly, "metodo_sitio": "osm_website"},
            {"rbd": 2, "sitio": "https://b.cl", "confianza_sitio": "baja", "paginas": 3, "montos": monthly, "metodo_sitio": "dominio_candidato"},
            {"rbd": 3, "sitio": None, "confianza_sitio": None, "paginas": 0, "montos": {}, "metodo_sitio": None},
            {"rbd": 4, "sitio": None, "confianza_sitio": None, "paginas": 0, "montos": {}, "metodo_sitio": None},
        ]}
        documents = [{"rbd": 1, "tipo": "pdf", "descargado": True, "con_texto": True, "con_montos": True}]
        report = fees.coverage_report(data, documents)
        self.assertEqual(report["embudo"]["con_sitio_web"], {"colegios": 2, "pct": 50.0})
        self.assertEqual(report["embudo"]["con_mensualidad_explicita_o_derivada"], {"colegios": 1, "pct": 25.0})
        self.assertEqual(report["embudo"]["mensualidad_2026_o_2027"]["colegios"], 1)
        self.assertEqual(report["documentos"]["colegios_cuyo_monto_viene_de_documento"]["colegios"], 1)


class UfTests(unittest.TestCase):
    def test_reads_the_day_from_the_month_table(self):
        page = (
            "<div class='meses' id='mes_octubre'><table><tr><th><strong>24</strong></th><td>41.000,00</td></tr></table></div>"
            "<div class='meses' id='mes_noviembre'><table><tr><th><strong>4</strong></th><td>41.268,88</td>"
            "<th><strong>24</strong></th><td width='200'>41.398,57</td></tr></table></div>"
        )
        self.assertEqual(fees.parse_uf(page, fees.datetime(2026, 11, 24)), 41398.57)


class CrawlHelperTests(unittest.TestCase):
    def test_parse_html_reads_links_text_and_skips_scripts(self):
        title, text, links = fees.parse_html('<title>Colegio</title><script>var x="Matrícula $1"</script><a href="/admision">Admisión 2026</a>', "https://c.cl/")
        self.assertEqual(title, "Colegio")
        self.assertNotIn("var x", text)
        self.assertEqual(links, [("https://c.cl/admision", "Admisión 2026")])

    def test_fee_links_rank_above_generic_ones(self):
        self.assertGreater(fees.link_score("https://c.cl/aranceles-2026", "Aranceles"), fees.link_score("https://c.cl/noticias", "Noticias"))

    def test_google_drive_links_become_downloads(self):
        self.assertEqual(
            fees.google_drive_download("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWx/view"),
            "https://drive.google.com/uc?export=download&id=1AbCdEfGhIjKlMnOpQrStUvWx",
        )


if __name__ == "__main__":
    unittest.main()
