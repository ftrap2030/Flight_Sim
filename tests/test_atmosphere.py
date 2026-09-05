"""ISA model checked against the published standard atmosphere tables."""

import math
import unittest

from flight_sim import atmosphere as atm


class TestISA(unittest.TestCase):
    def test_sea_level(self):
        self.assertAlmostEqual(atm.temperature_k(0), 288.15, places=2)
        self.assertAlmostEqual(atm.pressure_pa(0), 101325.0, places=1)
        self.assertAlmostEqual(atm.density(0), 1.225, places=3)
        self.assertAlmostEqual(atm.density_ratio(0), 1.0, places=4)

    def test_density_against_published_table(self):
        """Published ISA densities, kg/m^3."""
        for altitude_ft, expected in [
            (0, 1.2250),
            (5000, 1.0556),
            (10000, 0.9046),
            (20000, 0.6527),
            (30000, 0.4583),
            (36089, 0.3639),
        ]:
            self.assertAlmostEqual(
                atm.density(altitude_ft), expected, places=3,
                msg="density at {} ft".format(altitude_ft),
            )

    def test_temperature_lapse_and_tropopause(self):
        # 1.98 K per 1,000 ft through the troposphere.
        self.assertAlmostEqual(atm.temperature_k(10000), 288.15 - 19.812, places=2)
        # Isothermal above the tropopause.
        self.assertAlmostEqual(atm.temperature_k(40000), 216.65, places=2)
        self.assertAlmostEqual(atm.temperature_k(50000), 216.65, places=2)

    def test_pressure_continuous_across_tropopause(self):
        """The two pressure regimes must meet at 36,089 ft."""
        below = atm.pressure_pa(atm.TROPOPAUSE_FT - 1.0)
        above = atm.pressure_pa(atm.TROPOPAUSE_FT + 1.0)
        self.assertLess(abs(below - above), 5.0)

    def test_speed_of_sound(self):
        self.assertAlmostEqual(atm.speed_of_sound_ms(0), 340.29, places=1)
        # Constant above the tropopause, since it depends only on temperature.
        self.assertAlmostEqual(
            atm.speed_of_sound_ms(40000), atm.speed_of_sound_ms(45000), places=4
        )

    def test_ias_tas_relationship(self):
        """TAS exceeds IAS at altitude, and the conversions round-trip."""
        tas = 230.0
        for altitude in (0, 10000, 35000):
            ias = atm.tas_to_ias(tas, altitude)
            self.assertAlmostEqual(atm.ias_to_tas(ias, altitude), tas, places=6)
        # At sea level IAS and TAS coincide, to within the rounding in the ISA
        # constants themselves (sigma is 1.0 to about 8 significant figures).
        self.assertAlmostEqual(atm.tas_to_ias(tas, 0), tas, places=4)
        self.assertLess(atm.tas_to_ias(tas, 35000), tas)

    def test_mach_round_trip(self):
        for altitude in (0, 20000, 40000):
            tas = atm.mach_to_tas(0.78, altitude)
            self.assertAlmostEqual(atm.mach(tas, altitude), 0.78, places=9)

    def test_density_decreases_monotonically(self):
        previous = math.inf
        for altitude in range(0, 50000, 1000):
            current = atm.density(altitude)
            self.assertLess(current, previous)
            previous = current


if __name__ == "__main__":
    unittest.main()
