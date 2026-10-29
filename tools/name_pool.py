"""Invented-person name pools for the fixtures: common first and last names and towns per locale.

``make_data.py`` combines a first and a last name at random, so no record describes a real person.
The pools are fixed in the repository (no download, no package whose data changes between versions),
which keeps every fixture byte-identical on every machine. Accented letters are part of the data:
fixture 4 needs é, ñ, ü, ø and ç in Latin-1.
"""

from __future__ import annotations

LOCALES = ("en_GB", "en_IE", "de_DE", "nl_NL", "es_ES", "fr_FR", "da_DK")

# fmt: off
FIRST_NAMES: dict[str, tuple[str, ...]] = {
    "en_GB": (
        "Oliver", "Amelia", "Arthur", "Isla", "George", "Ava", "Noah", "Mia", "Jack", "Emily", "Charlie",
        "Grace", "Thomas", "Sophie", "James", "Lily", "William", "Evie", "Henry", "Poppy", "Daniel", "Chloe",
    ),
    "en_IE": (
        "Aoife", "Sean", "Ciara", "Conor", "Niamh", "Cian", "Saoirse", "Darragh", "Roisin", "Eoin",
        "Siobhan", "Padraig", "Orla", "Fionn", "Grainne", "Ronan",
    ),
    "de_DE": (
        "Lukas", "Laura", "Jonas", "Lea", "Felix", "Hannah", "Maximilian", "Sophie", "Paul", "Marie",
        "Jürgen", "Brigitte", "Günther", "Käthe", "Jörg", "Ute", "Dieter", "Renate",
    ),
    "nl_NL": (
        "Daan", "Emma", "Sem", "Julia", "Lucas", "Mila", "Levi", "Tess", "Jesse", "Sara", "Bram",
        "Anouk", "Ruben", "Fleur", "Thijs", "Lotte",
    ),
    "es_ES": ("José", "María", "Iñigo", "Begoña", "Andrés", "Lucía", "Martín", "Sofía", "Ramón", "Inés"),
    "fr_FR": ("François", "Hélène", "Céline", "Jérôme", "Agnès", "Frédéric", "Élodie", "Maël", "Noël"),
    "da_DK": ("Søren", "Mette", "Jørgen", "Birgitte", "Rasmus", "Sørine", "Bjørn", "Ida", "Frederik"),
}

LAST_NAMES: dict[str, tuple[str, ...]] = {
    "en_GB": (
        "Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Robinson", "Wright",
        "Thompson", "Evans", "Walker", "White", "Roberts", "Green", "Hall", "Wood", "Jackson", "Clarke",
        "Hughes", "Edwards", "Turner", "Harrison", "Cooper", "Ward", "Morris", "Kingsley", "Fletcher",
    ),
    "en_IE": (
        "Murphy", "Kelly", "O'Sullivan", "Walsh", "Byrne", "Ryan", "O'Brien", "Doyle", "McCarthy",
        "Gallagher", "O'Connor", "Kennedy", "Lynch", "Murray", "Quinn", "Moore", "McLoughlin",
    ),
    "de_DE": (
        "Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Schulz",
        "Hoffmann", "Schäfer", "Koch", "Bauer", "Richter", "Klein", "Wolf", "Schröder", "Neumann",
        "Schwarz", "Zimmermann", "Braun", "Krüger", "Hofmann", "Hartmann", "Lange", "Schmitt",
    ),
    "nl_NL": (
        "de Jong", "Jansen", "de Vries", "van den Berg", "van Dijk", "Bakker", "Janssen", "Visser",
        "Smit", "Meijer", "de Boer", "Mulder", "de Groot", "Bos", "Vos", "Peters", "Hendriks",
    ),
    "es_ES": (
        "Peña", "Muñoz", "García", "Fernández", "López", "Martínez", "Sánchez", "Pérez", "Ibáñez", "Gómez",
    ),
    "fr_FR": (
        "Lefèvre", "Girard", "Garçon", "Bérenger", "Mercier", "Lefebvre", "Rousseau", "Fontaine", "Ménard",
        "Barbé",
    ),
    "da_DK": (
        "Jørgensen", "Nielsen", "Hansen", "Pedersen", "Andersen", "Christensen", "Sørensen", "Møller",
        "Kjær", "Lund",
    ),
}

TOWNS: dict[str, tuple[str, ...]] = {
    "en_GB": ("Leeds", "Bristol", "York", "Norwich", "Derby", "Bath", "Exeter", "Chester"),
    "en_IE": ("Cork", "Galway", "Limerick", "Sligo", "Kilkenny"),
    "de_DE": ("Köln", "München", "Düsseldorf", "Nürnberg", "Lübeck", "Göttingen"),
    "nl_NL": ("Utrecht", "Leiden", "Delft", "Haarlem"),
    "es_ES": ("Logroño", "Córdoba", "Málaga", "Cádiz", "León"),
    "fr_FR": ("Besançon", "Orléans", "Nîmes", "Valence"),
    "da_DK": ("Århus", "Køge", "Næstved", "Odense"),
}
# fmt: on
