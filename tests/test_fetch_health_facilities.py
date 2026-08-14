import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_health_facilities.py"
SPEC = importlib.util.spec_from_file_location("fetch_health_facilities", SCRIPT)
health = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(health)


SAMPLE_MINSAL_ROW = {
    "EstablecimientoCodigo": "109100",
    "EstablecimientoGlosa": "Hospital Regional de Prueba",
    "TipoEstablecimientoGlosa": "Hospital",
    "NivelAtencionEstabglosa": "Tercer Nivel",
    "NivelComplejidadEstabGlosa": "Alta Complejidad",
    "DependenciaAdministrativa": "Servicio de Salud",
    "TipoSistemaSaludGlosa": "Público",
    "RegionCodigo": "13",
    "RegionGlosa": "Metropolitana de Santiago",
    "ComunaGlosa": "Santiago",
    "TipoViaGlosa": "Calle",
    "NombreVia": "Falsa",
    "Numero": "123",
    "TieneServicioUrgencia": "SI",
    "Latitud": "-33.45",
    "Longitud": "-70.65",
    "EstadoFuncionamiento": "Vigente en Operación Habitual",
}


class NivelComplejidadTests(unittest.TestCase):
    def test_known_levels(self):
        self.assertEqual(health.classify_size_by_nivel_complejidad("Alta Complejidad"), "grande")
        self.assertEqual(health.classify_size_by_nivel_complejidad("Mediana Complejidad"), "mediano")
        self.assertEqual(health.classify_size_by_nivel_complejidad("Baja Complejidad"), "chico")

    def test_unknown_or_missing(self):
        self.assertIsNone(health.classify_size_by_nivel_complejidad("No Aplica"))
        self.assertIsNone(health.classify_size_by_nivel_complejidad(None))
        self.assertIsNone(health.classify_size_by_nivel_complejidad(""))

    def test_case_insensitive(self):
        self.assertEqual(health.classify_size_by_nivel_complejidad("alta complejidad"), "grande")


class EstadoYRegionFilterTests(unittest.TestCase):
    def test_is_vigente_accepts_mixed_case_from_source(self):
        self.assertTrue(health.is_vigente({"EstadoFuncionamiento": "Vigente en Operación Habitual"}))
        self.assertTrue(health.is_vigente({"EstadoFuncionamiento": "Vigente en operación habitual"}))
        self.assertFalse(health.is_vigente({"EstadoFuncionamiento": "Cerrado"}))
        self.assertFalse(health.is_vigente({"EstadoFuncionamiento": ""}))

    def test_is_in_rm(self):
        self.assertTrue(health.is_in_rm({"RegionCodigo": "13"}))
        self.assertFalse(health.is_in_rm({"RegionCodigo": "05"}))
        self.assertFalse(health.is_in_rm({}))


class MinsalRowToFeatureTests(unittest.TestCase):
    def test_full_row_produces_expected_feature(self):
        feature = health.minsal_row_to_feature(SAMPLE_MINSAL_ROW)
        self.assertIsNotNone(feature)
        self.assertEqual(feature["geometry"], {"type": "Point", "coordinates": [-70.65, -33.45]})
        props = feature["properties"]
        self.assertEqual(props["categoria"], "salud")
        self.assertEqual(props["procedencia"], "minsal_deis")
        self.assertEqual(props["nombre"], "Hospital Regional de Prueba")
        self.assertEqual(props["tamano"], "grande")
        self.assertEqual(props["tamano_metodo"], "nivel_complejidad_minsal")
        self.assertTrue(props["tiene_urgencia"])
        self.assertEqual(props["direccion"], "Calle Falsa 123")

    def test_missing_coordinates_returns_none(self):
        row = {**SAMPLE_MINSAL_ROW, "Latitud": "", "Longitud": ""}
        self.assertIsNone(health.minsal_row_to_feature(row))

    def test_zero_coordinates_returns_none(self):
        row = {**SAMPLE_MINSAL_ROW, "Latitud": "0", "Longitud": "0"}
        self.assertIsNone(health.minsal_row_to_feature(row))

    def test_no_aplica_complejidad_is_honest_sin_dato(self):
        row = {**SAMPLE_MINSAL_ROW, "NivelComplejidadEstabGlosa": "No Aplica"}
        feature = health.minsal_row_to_feature(row)
        self.assertEqual(feature["properties"]["tamano"], "sin_dato")
        self.assertEqual(feature["properties"]["tamano_metodo"], "sin_dato")

    def test_urgencia_variants_are_normalized(self):
        for raw, expected in [("SI", True), ("NO", False), ("No", False), ("No Aplica", False)]:
            row = {**SAMPLE_MINSAL_ROW, "TieneServicioUrgencia": raw}
            feature = health.minsal_row_to_feature(row)
            self.assertEqual(feature["properties"]["tiene_urgencia"], expected, msg=raw)


class PharmacyClassificationTests(unittest.TestCase):
    def test_known_chain_is_chico(self):
        self.assertEqual(health.classify_pharmacy_size_by_brand("Cruz Verde Ñuñoa"), "chico")
        self.assertEqual(health.classify_pharmacy_size_by_brand("Salcobrand"), "chico")

    def test_unknown_name_returns_none(self):
        self.assertIsNone(health.classify_pharmacy_size_by_brand("Botica del Barrio"))
        self.assertIsNone(health.classify_pharmacy_size_by_brand(None))

    def test_node_without_area_uses_brand_heuristic(self):
        element = {
            "type": "node",
            "id": 42,
            "lon": -70.6,
            "lat": -33.4,
            "tags": {"amenity": "pharmacy", "name": "Cruz Verde"},
        }
        feature = health.pharmacy_element_to_feature(element)
        self.assertEqual(feature["properties"]["tamano"], "chico")
        self.assertEqual(feature["properties"]["tamano_metodo"], "marca_conocida")
        self.assertEqual(feature["properties"]["procedencia"], "osm")

    def test_node_without_area_or_known_brand_is_sin_dato(self):
        element = {
            "type": "node",
            "id": 43,
            "lon": -70.6,
            "lat": -33.4,
            "tags": {"amenity": "pharmacy", "name": "Farmacia Independiente"},
        }
        feature = health.pharmacy_element_to_feature(element)
        self.assertEqual(feature["properties"]["tamano"], "sin_dato")
        self.assertEqual(feature["properties"]["tamano_metodo"], "sin_dato")


if __name__ == "__main__":
    unittest.main()
