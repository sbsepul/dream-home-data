import importlib.util
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_school_profiles.py"
SPEC = importlib.util.spec_from_file_location("build_school_profiles", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


def directory_row(**overrides):
    row = {
        "AGNO": "2026",
        "RBD": "100",
        "DGV_RBD": "9",
        "NOM_RBD": "COLEGIO PRUEBA",
        "RUT_SOSTENEDOR": "76000000",
        "P_JURIDICA": "1",
        "COD_REG_RBD": "13",
        "NOM_REG_RBD_A": "RM",
        "COD_COM_RBD": "13123",
        "NOM_COM_RBD": "PROVIDENCIA",
        "COD_DEPE": "3",
        "RURAL_RBD": "0",
        "LATITUD": "   -33,4300000",
        "LONGITUD": "   -70,6100000",
        "CONVENIO_PIE": "1",
        "PACE": "0",
        "ENS_01": "10",
        "ENS_02": "110",
        "ENS_03": "310",
        "ENS_04": "0",
        "MAT_ENS_1": "40",
        "MAT_ENS_2": "300",
        "MAT_ENS_5": "160",
        "MAT_TOTAL": "500",
        "ESTADO_ESTAB": "1",
        "ORI_RELIGIOSA": "2",
        "PAGO_MATRICULA": "GRATUITO",
        "PAGO_MENSUAL": "$25.001 A $50.000",
    }
    row.update(overrides)
    return row


def csv_text(rows, delimiter=";"):
    columns = list(rows[0])
    lines = [delimiter.join(columns)] + [delimiter.join(str(row.get(column, "")) for column in columns) for row in rows]
    return "\n".join(lines) + "\n"


def write_release(archive_root: Path, source_id: str, release_id: str, files: dict[str, str]) -> None:
    release_dir = archive_root / source_id / release_id
    release_dir.mkdir(parents=True)
    with zipfile.ZipFile(release_dir / "source.zip", "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content.encode("utf-8"))
    (release_dir / "metadata.json").write_text(
        json.dumps({"source_id": source_id, "release_id": release_id, "upstream_release_id": release_id, "sha256": f"{source_id}{release_id}".ljust(16, "0")}),
        encoding="utf-8",
    )


class DirectoryTests(unittest.TestCase):
    def test_keeps_official_dependency_and_derives_sector(self):
        profiles = builder.directory_profiles([
            directory_row(RBD="1", COD_DEPE="2"),
            directory_row(RBD="2", COD_DEPE="6"),
            directory_row(RBD="3", COD_DEPE="4"),
            directory_row(RBD="4", COD_DEPE="5"),
        ])
        self.assertEqual(profiles[1]["dependencia"], {"codigo_oficial": 2, "nombre_oficial": "Municipal DAEM", "sector": "publico"})
        self.assertEqual(profiles[2]["dependencia"]["sector"], "publico")
        self.assertEqual(profiles[3]["dependencia"]["sector"], "particular_pagado")
        self.assertEqual(profiles[4]["dependencia"]["sector"], "administracion_delegada")

    def test_skips_closed_schools_and_parses_location_and_levels(self):
        profiles = builder.directory_profiles([directory_row(), directory_row(RBD="200", ESTADO_ESTAB="3")])
        self.assertEqual(list(profiles), [100])
        school = profiles[100]
        self.assertEqual(school["ubicacion"], {"lat": -33.43, "lon": -70.61})
        self.assertTrue(school["niveles"]["basica"] and school["niveles"]["media_hc"])
        self.assertFalse(school["niveles"]["media_tp"])
        self.assertEqual(school["matricula"]["media"], 160)
        self.assertEqual(school["orientacion_religiosa"], "catolica")

    def test_option_four_pie_is_not_special_or_adult_education(self):
        school = builder.directory_profiles([directory_row(ENS_01="299", ENS_02="110", ENS_03="0")])[100]
        self.assertFalse(school["niveles"]["especial"])
        self.assertFalse(school["niveles"]["adultos"])

    def test_payment_bands_keep_range_and_unknown_is_null(self):
        school = builder.directory_profiles([directory_row()])[100]
        self.assertEqual(school["pago"]["mensualidad"]["min_clp"], 25_001)
        self.assertEqual(school["pago"]["mensualidad"]["max_clp"], 50_000)
        self.assertEqual(school["pago"]["matricula"]["tramo"], "gratuito")
        self.assertIsNone(builder.payment_band("SIN INFORMACION"))
        self.assertIsNone(builder.payment_band("MAS DE $100.000")["max_clp"])

    def test_invalid_coordinates_are_dropped(self):
        school = builder.directory_profiles([directory_row(LATITUD="0", LONGITUD="0")])[100]
        self.assertIsNone(school["ubicacion"])


class CourseTests(unittest.TestCase):
    def course(self, ense, grade, students, **per_grade):
        row = {"RBD": "100", "COD_ENSE": str(ense), "COD_GRADO": str(grade), "N_ALU": str(students)}
        for index in range(1, 9):
            row[f"N_ALU_GRADO{index}"] = str(per_grade.get(f"g{index}", "NA"))
        return row

    def test_full_cycle_from_first_basic_to_fourth_secondary(self):
        rows = [self.course(110, grade, 30) for grade in range(1, 9)] + [self.course(310, grade, 20) for grade in range(1, 5)]
        profile = builder.course_profiles(rows)[100]
        self.assertTrue(profile["imparte_1b_a_4m"])
        self.assertEqual(profile["rango"], "1B-4M")
        self.assertEqual(profile["cursos_escolares"], 12)
        self.assertEqual(profile["alumnos_por_curso"], 26.7)

    def test_combined_courses_use_per_grade_counts(self):
        rows = [self.course(10, "NA", 23, g4=13, g5=10), self.course(110, "NA", 12, g1=5, g3=7)]
        profile = builder.course_profiles(rows)[100]
        self.assertEqual(profile["grados"], ["PK", "K", "1B", "3B"])
        self.assertEqual(profile["rango"], "PK,K,1B,3B")
        self.assertFalse(profile["imparte_basica_completa"])

    def test_empty_courses_do_not_count(self):
        self.assertEqual(builder.course_profiles([self.course(110, 1, 0)]), {})


class PerformanceTests(unittest.TestCase):
    def test_retirement_rate_excludes_transfers_and_adult_programs(self):
        rows = [
            {"RBD": "100", "COD_ENSE2": "2", "APR_HOM_01": "40", "APR_MUJ_01": "45", "REP_HOM_01": "5", "RET_MUJ_02": "10", "TRA_HOM_01": "7", "SI_HOM_01": "1", "PROM_ASIS": "90"},
            {"RBD": "100", "COD_ENSE2": "5", "APR_SI_01": "", "APR_HOM_01": "100", "PROM_ASIS": "80"},
            {"RBD": "100", "COD_ENSE2": "3", "RET_HOM_01": "50"},
        ]
        profile = builder.performance_profiles(rows)[100]
        self.assertEqual(profile["base_situacion_final"], 200)
        self.assertEqual(profile["retirados"], 10)
        self.assertEqual(profile["trasladados"], 7)
        self.assertEqual(profile["tasa_retiro_pct"], 5.0)
        self.assertEqual(profile["tasa_aprobacion_pct"], 92.5)
        # Ponderada por evaluados: (90*90 + 80*100) / 190.
        self.assertEqual(profile["asistencia_promedio_pct"], 84.7)


class PaesTests(unittest.TestCase):
    def participant(self, rbd="100", egress="2025", nem="700", reading="600", math="620"):
        return {"ANYO_PROCESO": "2026", "ANYO_DE_EGRESO": egress, "RBD": rbd, "PTJE_NEM": nem, "PTJE_RANKING": nem, "PROMEDIO_NOTAS": "6,1", "CLEC_REG_ACTUAL": reading, "MATE1_REG_ACTUAL": math}

    def test_uses_recent_cohort_and_only_tested_students_for_scores(self):
        rows = [self.participant() for _ in range(9)] + [self.participant(reading="0", math="0"), self.participant(egress="2019", nem="200")]
        profile = builder.paes_profiles(rows)[100]
        self.assertEqual(profile["inscritos"], 10)
        self.assertEqual(profile["promedio_nem"], 700.0)
        self.assertEqual(profile["promedio_notas"], 6.1)
        self.assertIsNone(profile["rindieron_cl_m1"])
        self.assertIsNone(profile["promedio_cl_m1"])

    def test_small_cohorts_are_suppressed(self):
        profile = builder.paes_profiles([self.participant() for _ in range(3)])[100]
        self.assertTrue(profile["suprimido"])
        self.assertIsNone(profile["inscritos"])
        self.assertIsNone(profile["promedio_nem"])

    def test_scores_over_600_share(self):
        rows = [self.participant(reading="650", math="650") for _ in range(5)] + [self.participant(reading="500", math="500") for _ in range(5)]
        profile = builder.paes_profiles(rows)[100]
        self.assertEqual(profile["promedio_cl_m1"], 575.0)
        self.assertEqual(profile["pct_cl_m1_sobre_600"], 50.0)


class ComplaintTests(unittest.TestCase):
    def test_counts_by_scope_and_topic(self):
        rows = [
            {"AGNO": "2025", "RBD": "100", "DEN_AMBITO": "CONVIVENCIA", "DEN_TEMA": "MALTRATO A PÁRVULOS Y/O ESTUDIANTES"},
            {"AGNO": "2025", "RBD": "100", "DEN_AMBITO": "CONVIVENCIA", "DEN_TEMA": "SITUACIONES DE CONNOTACIÓN SEXUAL"},
            {"AGNO": "2025", "RBD": "100", "DEN_AMBITO": "INFRAESTRUCTURA", "DEN_TEMA": "LOCAL"},
            {"AGNO": "2025", "RBD": " ", "DEN_AMBITO": "CONVIVENCIA", "DEN_TEMA": "X"},
        ]
        profile = builder.complaint_profiles(rows)[100]
        self.assertEqual((profile["total"], profile["convivencia"], profile["maltrato"], profile["connotacion_sexual"], profile["infraestructura"]), (3, 2, 1, 1, 1))

    def test_schools_without_complaints_get_zero_with_denominator(self):
        directory = builder.directory_profiles([directory_row()])
        school = builder.assemble(directory, results_enrollment={100: 400}, results_year=2025, complaints={})[0]
        self.assertEqual(school["denuncias"]["total"], 0)
        self.assertEqual(school["denuncias"]["por_100_estudiantes"], 0.0)


class SimceIdpsTests(unittest.TestCase):
    def test_simce_reads_subjects_and_keeps_missing_as_null(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "simce8b2025_rbd_final.csv"
            path.write_text(csv_text([
                {"rbd": "100", "cod_grupo": "4", "nalu_lect8b_rbd": "50", "prom_lect8b_rbd": "250", "dif_lect8b_rbd": "-3", "sigdif_lect8b_rbd": "0", "palu_eda_ade_lect8b_rbd": "30.5", "marca_lect8b_rbd": "", "prom_mate8b_rbd": "", "marca_mate8b_rbd": ""},
            ]), encoding="latin-1")
            profile = builder.simce_profiles([path])[100]
        self.assertEqual(profile["gse"], 4)
        self.assertEqual(len(profile["resultados"]), 1)
        result = profile["resultados"][0]
        self.assertEqual((result["grado"], result["asignatura"], result["puntaje"], result["pct_adecuado"]), ("8B", "lectura", 250, 30.5))

    def test_idps_extracts_safe_environment_dimension(self):
        with TemporaryDirectory() as temporary:
            indicators = Path(temporary) / "idps2M2025_rbd_final.csv"
            dimensions = Path(temporary) / "idps2M2025_rbd_dim_final.csv"
            indicators.write_text(csv_text([{"rbd": "100", "id_indicador": "2", "prom": "78", "dif": "1", "sigdif": "0", "difgru": "2"}]), encoding="utf-8")
            dimensions.write_text(csv_text([
                {"rbd": "100", "id_indicador": "2", "id_dimension": "23", "prom": "74"},
                {"rbd": "100", "id_indicador": "2", "id_dimension": "21", "prom": "60"},
            ]), encoding="utf-8")
            profile = builder.idps_profiles([indicators], [dimensions])[100]
        self.assertEqual(profile["indicadores"]["clima_convivencia"]["2M"]["puntaje"], 78.0)
        self.assertEqual(profile["ambiente_seguro"], {"2M": 74.0})


class LanguageTests(unittest.TestCase):
    def subject(self, ense, grade, name, plan="0", rbd="100"):
        return {"RBD": rbd, "COD_ENSE": str(ense), "COD_GRADO": str(grade), "NOM_SUBSECTOR": name, "TIPO_PLAN_EST": plan, "MRUN": "999"}

    def test_english_grades_early_start_and_intensity(self):
        rows = [self.subject(110, 1, "INGLÉS 1° BÁSICO"), self.subject(110, 1, "TALLER DE INGLÉS"), self.subject(110, 5, "IDIOMA EXTRANJERO: INGLÉS"), self.subject(310, 1, "IDIOMA EXTRANJERO (INGLÉS)")]
        profile = builder.language_profiles(rows)[100]
        self.assertEqual(profile["ingles_grados"], ["1B", "5B", "1M"])
        self.assertEqual(profile["ingles_desde"], "1B")
        self.assertTrue(profile["ingles_temprano"])
        self.assertTrue(profile["ingles_intensivo"])

    def test_mandatory_english_only_is_not_early(self):
        profile = builder.language_profiles([self.subject(110, 5, "IDIOMA EXTRANJERO: INGLES")])[100]
        self.assertFalse(profile["ingles_temprano"])
        self.assertFalse(profile["ingles_intensivo"])

    def test_other_and_indigenous_languages_without_false_friends(self):
        rows = [
            self.subject(110, 3, "IDIOMA EXTRANJERO (ALEMÁN)", plan="1"),
            self.subject(110, 3, "CULTURA RELIGIOSA FRANCISCANA"),
            self.subject(310, 2, "CHILE Y LA REGIÓN LATINOAMERICANA"),
            self.subject(110, 2, "LENGUA INDÍGENA: MAPUZUGUN"),
            self.subject(110, 2, "LENGUA Y LITERATURA"),
        ]
        profile = builder.language_profiles(rows)[100]
        self.assertEqual(profile["otros_idiomas"], ["aleman"])
        self.assertEqual(profile["lengua_indigena"], ["mapuzugun"])
        self.assertTrue(profile["plan_propio"])
        self.assertFalse(profile["ingles"])

    def test_adult_courses_are_ignored(self):
        self.assertEqual(builder.language_profiles([self.subject(363, 1, "INGLÉS")]), {})

    def test_name_hints(self):
        self.assertEqual(builder.name_language_hints("DEUTSCHE SCHULE SANTIAGO"), ["aleman"])
        self.assertEqual(builder.name_language_hints("LYCÉE ANTOINE DE SAINT-EXUPÉRY"), ["frances"])
        self.assertEqual(builder.name_language_hints("LICEO BICENTENARIO"), [])


class ReleaseTests(unittest.TestCase):
    def test_latest_release_wins_and_revision_beats_original(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, upstream, fetched in (("2024", "2024", "2025-01-01"), ("2025", "2025", "2025-06-01"), ("2025-revision-abc", "2025", "2025-09-01")):
                directory = root / "src" / name
                directory.mkdir(parents=True)
                (directory / "metadata.json").write_text(json.dumps({"upstream_release_id": upstream, "fetched_at": fetched}))
            self.assertEqual(builder.find_release(root, "src").release_id, "2025-revision-abc")
            self.assertEqual(builder.find_release(root, "src", "2024").release_id, "2024")
            self.assertIsNone(builder.find_release(root, "missing"))


class EndToEndTests(unittest.TestCase):
    def test_builds_outputs_without_individual_rows(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "raw"
            write_release(archive, "mineduc_school_directory", "2026", {"Directorio_Oficial_EE_2026.csv": csv_text([directory_row()])})
            write_release(archive, "mineduc_school_directory", "2025", {"Directorio_Oficial_EE_2025.csv": csv_text([directory_row(MAT_TOTAL="400")])})
            participants = [
                {"MRUN": str(9_000 + index), "ANYO_PROCESO": "2026", "ANYO_DE_EGRESO": "2025", "RBD": "100", "PTJE_NEM": "700", "PTJE_RANKING": "710", "PROMEDIO_NOTAS": "6,0", "CLEC_REG_ACTUAL": "600", "MATE1_REG_ACTUAL": "600"}
                for index in range(12)
            ]
            write_release(archive, "mineduc_paes_scores", "2026", {"A_INSCRITOS_PUNTAJES_PAES_2026_PUB_MRUN.csv": csv_text(participants)})
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as bundle:
                bundle.writestr(
                    "20251231_DENUNCIAS_2025_PUBL.csv",
                    csv_text([{"AGNO": "2025", "DEN_MRUN": "123456", "RBD": "100", "DEN_AMBITO": "CONVIVENCIA", "DEN_TEMA": "DISCRIMINACIÓN"}]),
                )
            release_dir = archive / "supereduc_complaints" / "2025"
            release_dir.mkdir(parents=True)
            with zipfile.ZipFile(release_dir / "source.zip", "w") as bundle:
                bundle.writestr("DEN_2025/Denuncias 2025.zip", inner.getvalue())
            (release_dir / "metadata.json").write_text(json.dumps({"upstream_release_id": "2025", "sha256": "f" * 64}))

            schools, manifest = builder.build(archive, root / "out", root / "cache")
            paths = builder.write_outputs(schools, manifest, root / "out")

            school = schools[0]
            self.assertEqual(school["paes"]["promedio_nem"], 700.0)
            self.assertEqual(school["denuncias"]["discriminacion"], 1)
            self.assertEqual(school["denuncias"]["por_100_estudiantes"], 0.2)
            self.assertEqual(manifest["anio_resultados"], 2025)
            self.assertIn("mineduc_school_directory:2025", manifest["fuentes"])
            geojson = json.loads(paths["geojson"].read_text(encoding="utf-8"))
            self.assertEqual(geojson["features"][0]["geometry"]["coordinates"], [-70.61, -33.43])
            for path in paths.values():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("MRUN", text)
                self.assertNotIn("9005", text)

    def test_missing_directory_fails_with_instructions(self):
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(builder.BuildError, "archive_education_source.py mineduc_school_directory"):
                builder.build(Path(temporary), Path(temporary) / "out", Path(temporary) / "cache")


if __name__ == "__main__":
    unittest.main()
