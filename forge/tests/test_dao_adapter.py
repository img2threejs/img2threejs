from __future__ import annotations

import unittest

from forge.stage2_spec.dao_adapter import (
    DaoDimensions,
    assemble_dao_dimensions,
    get_dao_family_adapter,
    validate_dao_component_tree,
)


class DaoAdapterTests(unittest.TestCase):
    def dimensions(self, **overrides) -> DaoDimensions:
        values = {
            "blade_length": 1.8,
            "blade_thickness": 0.05,
            "guard_kind": "disk",
            "guard_diameter": 0.12,
            "guard_thickness": 0.01,
            "front_ferrule_length": 0.02,
            "front_ferrule_diameter": 0.08,
            "handle_kind": "cord-wrap",
            "handle_length": 0.3,
            "handle_diameter": 0.09,
            "rear_ferrule_length": 0.02,
            "rear_ferrule_diameter": 0.08,
            "pommel_kind": "ring",
            "pommel_length": 0.16,
            "inlay_count": 6,
        }
        values.update(overrides)
        return DaoDimensions(**values)

    def test_han_huan_shou_contract_names_stable_slots_and_views(self) -> None:
        contract = get_dao_family_adapter("han-huan-shou").component_tree_contract()
        self.assertEqual(contract["family"], "dao")
        self.assertEqual(contract["subtype"], "han-huan-shou")
        self.assertEqual(contract["slots"], ("blade", "guard", "front-ferrule", "handle", "rear-ferrule", "pommel"))
        self.assertIn("true-side", contract["reviewViewpoints"])
        self.assertEqual(contract["slotComponents"]["front-ferrule"], "collar")
        self.assertEqual(contract["slotSockets"]["rear-ferrule"], "handle-back")
        self.assertEqual(contract["integralComponentOwners"]["stud-f"], "handle")
        self.assertEqual(contract["integralComponentOwners"]["stud-seat-f"], "handle")
        self.assertEqual(contract["integralComponentOwners"]["ring-engraving-middle"], "ring")

    def test_layout_orders_parts_and_evenly_places_six_inlays(self) -> None:
        layout = assemble_dao_dimensions(self.dimensions())
        self.assertEqual(layout["blade"]["heelX"], 1.8)
        self.assertLess(layout["guard"]["x"], layout["handle"]["x"])
        self.assertLess(layout["handle"]["x"], layout["pommel"]["x"])
        self.assertEqual(len(layout["inlayXs"]), 6)
        spacings = [b - a for a, b in zip(layout["inlayXs"], layout["inlayXs"][1:])]
        self.assertTrue(all(abs(value - spacings[0]) < 1e-12 for value in spacings))

    def test_layout_rejects_unknown_part_variants_and_invalid_sizes(self) -> None:
        with self.assertRaisesRegex(ValueError, "guard kind"):
            assemble_dao_dimensions(self.dimensions(guard_kind="basket"))
        with self.assertRaisesRegex(ValueError, "must be positive"):
            assemble_dao_dimensions(self.dimensions(blade_thickness=0.0))
        with self.assertRaisesRegex(ValueError, "unsupported-subtype"):
            get_dao_family_adapter("unknown")

    def test_component_tree_validation_requires_slots_and_integral_ownership(self) -> None:
        adapter = get_dao_family_adapter("han-huan-shou")
        socket_by_slot = dict(adapter.slot_sockets)
        components = [
            {
                "id": component_id,
                **(
                    {"attachment": {"parentSocket": socket_by_slot[slot]}}
                    if slot in socket_by_slot
                    else {}
                ),
            }
            for slot, component_id in adapter.slot_components
        ]
        for component_id, owner_id in adapter.integral_component_owners:
            components.append(
                {
                    "id": component_id,
                    "explodeWithParent": owner_id,
                    "actionProfile": {"destruction": {"fractureGroup": owner_id}},
                }
            )
        self.assertEqual(validate_dao_component_tree(adapter, components), [])

        components[-1]["explodeWithParent"] = "root"
        failures = validate_dao_component_tree(adapter, components)
        self.assertTrue(any("must explode with" in failure for failure in failures))

        without_blade = [component for component in components if component["id"] != "blade"]
        failures = validate_dao_component_tree(adapter, without_blade)
        self.assertTrue(any("slot 'blade'" in failure for failure in failures))

        wrong_socket = [dict(component) for component in components]
        next(component for component in wrong_socket if component["id"] == "handle")["attachment"] = {
            "parentSocket": "blade-heel"
        }
        failures = validate_dao_component_tree(adapter, wrong_socket)
        self.assertTrue(any("slot 'handle'" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
