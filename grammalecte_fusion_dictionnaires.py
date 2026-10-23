#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fusion de vocabulaires TSV et de dictionnaires Grammalecte JSON en un dictionnaire compilé.

Ce module fusionne des vocabulaires TSV et des dictionnaires Grammalecte JSON en un seul
dictionnaire compilé (format ``/grammalecte-fsa/``), sans doublons, trié sans tenir compte
de la casse. Le dictionnaire produit s'importe dans l'éditeur lexical de l'extension Grammalecte.

Sans paramètre, le script affiche son aide (comme avec ``--aide``).

Formats d'entrée
================
``*.tsv``
    Colonnes ``flexion <TAB> lemme <TAB> étiquettes``. L'en-tête, les lignes vides et
    les lignes commençant par ``#`` sont ignorés.
``*.json``
    Dictionnaire compilé Grammalecte (par exemple un export de l'éditeur lexical).

Nom par défaut
==============
Le dictionnaire s'appelle ``Dictionnaire_personnel_AAAA-MM-JJ`` (date du jour) et le
fichier produit ``fr.Dictionnaire_personnel_AAAA-MM-JJ.json``.

Fusion de la casse
==================
Désactivable avec ``--conserver-casse``. Grammalecte accepte « Wazuh » et « WAZUH » si
« wazuh » est dans le dictionnaire, donc :

* une flexion en casse titre (``Wazuh``) est convertie en minuscules (``wazuh``) ;
* une flexion en capitales (``WAZUH``) est supprimée si sa version minuscule existe ;
* les graphies mixtes (``OpenWrt``, ``macOS``, ``PeerTube``) sont conservées telles quelles.

Avertissements
==============
Une même flexion portant plusieurs étiquettes différentes est signalée sur la sortie
d'erreur. Toutes les étiquettes sont conservées.

Exemple
=======
::

    grammalecte_fusion_dictionnaires.py "Vocabulaire sysadmin.tsv" mes_mots.tsv ancien.json

Dans ce module, une *entrée* est un triplet ``(flexion, lemme, étiquettes)``.
"""
import argparse
import datetime
import json
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

Entree = Tuple[str, str, str]
"""Entrée de lexique : ``(flexion, lemme, étiquettes)``."""


# ---------- codes de radical (miroir de str_transform.js) ----------

def definir_code_suffixe(flexion: str, radical: str) -> str:
    """Calcule le code de radical qui permet de retrouver ``radical`` depuis ``flexion``.

    Le code est ``"0"`` si les deux mots sont identiques. Sinon, son premier caractère
    donne (via son code ASCII moins 48) le nombre de lettres à retirer à la fin de la
    flexion, et le reste est le suffixe à ajouter.

    :param flexion: forme fléchie du mot.
    :param radical: lemme (forme de base) du mot.
    :return: le code de suffixe, par exemple ``"0"`` ou ``"1er"``.
    """
    if flexion == radical:
        return "0"
    communs = 0
    for a, b in zip(flexion, radical):
        if a != b:
            break
        communs += 1
    return chr(len(flexion) - communs + 48) + radical[communs:]


def appliquer_code_suffixe(mot: str, code: str) -> str:
    """Retrouve le radical d'un mot à partir d'un code de suffixe (inverse de :func:`definir_code_suffixe`).

    :param mot: forme fléchie.
    :param code: code de suffixe.
    :return: le radical correspondant.
    """
    if code == "0":
        return mot
    if code[0] == "0":
        return mot + code[1:]
    return mot[:-(ord(code[0]) - 48)] + code[1:]


def appliquer_code_affixe(mot: str, code: str) -> str:
    """Retrouve le radical d'un mot à partir d'un code d'affixe (préfixe et suffixe).

    Utilisé pour lire les dictionnaires dont le champ ``cStemming`` vaut ``"A"``.

    :param mot: forme fléchie.
    :param code: code d'affixe (``"0"``, un radical brut, ou ``"n(pfx)/m(sfx)"``).
    :return: le radical correspondant.
    """
    if code == "0":
        return mot
    if "/" not in code:
        return code
    prefixe, suffixe = code.split("/", 1)
    mot = prefixe[1:] + mot[ord(prefixe[0]) - 48:]
    if suffixe[0] == "0":
        return mot + suffixe[1:]
    return mot[:-(ord(suffixe[0]) - 48)] + suffixe[1:]


# ---------- lecture ----------

def normaliser(texte: str) -> str:
    """Normalise un texte comme Grammalecte avant une recherche.

    Retire les espaces de bordure, passe en NFC et remplace l'apostrophe droite par
    l'apostrophe typographique ``’``.

    :param texte: texte à normaliser.
    :return: le texte normalisé.
    """
    return unicodedata.normalize("NFC", texte.strip().replace("'", "’"))


def lire_tsv(chemin: Path) -> List[Entree]:
    """Lit un vocabulaire au format TSV.

    Les lignes mal formées (moins de 3 colonnes, flexion vide, étiquettes ne commençant
    pas par ``:``) sont signalées sur la sortie d'erreur puis ignorées. Si le lemme est
    vide, il prend la valeur de la flexion.

    :param chemin: chemin du fichier ``.tsv``.
    :return: la liste des entrées lues.
    """
    entrees = []
    with open(chemin, encoding="utf-8") as fichier:
        for numero, ligne in enumerate(fichier, 1):
            ligne = ligne.rstrip("\r\n")
            if not ligne.strip() or ligne.lstrip().startswith("#"):
                continue
            colonnes = ligne.split("\t")
            if numero == 1 and colonnes[0].strip().lower() in ("flexion", "flex"):
                continue  # en-tête
            if len(colonnes) < 3:
                print(f"[!] {chemin}:{numero} : moins de 3 colonnes, ligne ignorée", file=sys.stderr)
                continue
            flexion, lemme, etiquettes = normaliser(colonnes[0]), normaliser(colonnes[1]), colonnes[2].strip()
            if not flexion or not etiquettes.startswith(":"):
                print(f"[!] {chemin}:{numero} : flexion vide ou étiquettes invalides ({etiquettes!r}), ligne ignorée",
                      file=sys.stderr)
                continue
            entrees.append((flexion, lemme or flexion, etiquettes))
    return entrees


def lire_json(chemin: Path) -> List[Entree]:
    """Lit un dictionnaire compilé Grammalecte et en extrait toutes les entrées.

    :param chemin: chemin du fichier ``.json``.
    :return: la liste des entrées du dictionnaire.
    :raises ValueError: si le fichier n'est pas un dictionnaire ``grammalecte-fsa`` ou n'a pas de données binaires.
    :raises KeyError: si un champ obligatoire est absent.
    :raises json.JSONDecodeError: si le fichier n'est pas du JSON valide.
    """
    with open(chemin, encoding="utf-8") as fichier:
        dico = json.load(fichier)
    if not str(dico.get("sHeader", "")).startswith(("/grammalecte-fsa/", "/pyfsa/")):
        raise ValueError("pas un dictionnaire grammalecte-fsa")
    return decoder_dictionnaire(dico)


def decoder_dictionnaire(dico: Dict[str, Any]) -> List[Entree]:
    """Relit toutes les entrées d'un dictionnaire compilé (graphe de mots indexable).

    Parcourt le graphe en profondeur : les arcs dont la valeur est inférieure ou égale à
    ``nChar`` sont des caractères ; les suivants sont des codes de radical, puis des étiquettes.
    Gère le format actuel (``lByDic``) et l'ancien format hexadécimal (``sByDic``).

    :param dico: contenu JSON du dictionnaire.
    :return: la liste des entrées ``(flexion, lemme, étiquettes)``.
    :raises ValueError: si ni ``lByDic`` ni ``sByDic`` ne sont présents (fichier tronqué).
    """
    octets_arc, octets_adresse = dico["nBytesArc"], dico["nBytesNodeAddress"]
    if "lByDic" not in dico and "sByDic" not in dico:
        raise ValueError("pas de données binaires (lByDic/sByDic) : fichier tronqué ou non exporté par Grammalecte")
    if "lByDic" in dico:
        arcs = dico["lByDic"]
    else:  # ancien format hexadécimal
        brut = bytes.fromhex(dico["sByDic"])
        diviseur = (octets_arc + octets_adresse) / 2
        arcs, i, pas = [], 0, octets_arc + octets_adresse
        while i < len(brut):
            arcs.append(int.from_bytes(brut[i:i + octets_arc], "big"))
            arcs.append(round(int.from_bytes(brut[i + octets_arc:i + pas], "big") / diviseur))
            i += pas
    masque_arc = (1 << (octets_arc * 8 - 3)) - 1
    masque_dernier = 1 << (octets_arc * 8 - 2)
    nb_caracteres = dico["nChar"]
    valeurs = dico["lArcVal"]
    appliquer_code = appliquer_code_affixe if dico["cStemming"] == "A" else appliquer_code_suffixe

    def arcs_du_noeud(adresse: int) -> Iterator[Tuple[int, int]]:
        """Énumère les arcs d'un nœud sous la forme ``(valeur de l'arc, adresse du nœud suivant)``."""
        while True:
            brut_arc, suivant = arcs[adresse], arcs[adresse + 1]
            yield brut_arc & masque_arc, suivant
            if brut_arc & masque_dernier:
                return
            adresse += 2

    entrees: List[Entree] = []

    def parcourir(adresse: int, mot: str) -> None:
        """Descend récursivement dans le graphe en accumulant les caractères du mot."""
        for valeur, suivant in arcs_du_noeud(adresse):
            if valeur <= nb_caracteres:
                parcourir(suivant, mot + valeurs[valeur])
            else:
                radical = appliquer_code(mot, valeurs[valeur])
                for etiquette, _ in arcs_du_noeud(suivant):
                    entrees.append((mot, radical, valeurs[etiquette]))

    sys.setrecursionlimit(10000)
    parcourir(0, "")
    return entrees


# ---------- fusion de la casse ----------

def est_casse_titre(mot: str) -> bool:
    """Indique si un mot est en casse titre : initiale en capitale, reste en minuscules (``Wazuh``, ``Pi-hole``).

    :param mot: mot à tester.
    :return: ``True`` si le mot est en casse titre.
    """
    return len(mot) > 1 and mot[0].isupper() and mot[1:] == mot[1:].lower() and mot[1:] != mot[1:].upper()


def est_en_capitales(mot: str) -> bool:
    """Indique si un mot est entièrement en capitales (``WAZUH``, ``DNS``).

    :param mot: mot à tester.
    :return: ``True`` si le mot est en capitales et contient au moins une lettre.
    """
    return len(mot) > 1 and mot == mot.upper() and mot != mot.lower()


def fusionner_casse(entrees: List[Entree]) -> Tuple[List[Entree], int, int]:
    """Fusionne les variantes de casse d'une même flexion.

    Les flexions en casse titre passent en minuscules (le lemme aussi, s'il est en casse
    titre). Ensuite, les flexions en capitales sont supprimées quand leur version
    minuscule existe. Les graphies mixtes sont conservées.

    :param entrees: entrées sans doublon exact.
    :return: un triplet ``(entrées conservées, nombre de flexions converties en minuscules,
             nombre de flexions en capitales supprimées)``.
    """
    nb_converties, resultat = 0, []
    for flexion, lemme, etiquettes in entrees:
        if est_casse_titre(flexion):
            flexion = flexion.lower()
            if est_casse_titre(lemme):
                lemme = lemme.lower()
            nb_converties += 1
        resultat.append((flexion, lemme, etiquettes))
    resultat = list(dict.fromkeys(resultat))
    minuscules = {f for f, _, _ in resultat if f == f.lower()}
    conservees = [e for e in resultat if not (est_en_capitales(e[0]) and e[0].lower() in minuscules)]
    return conservees, nb_converties, len(resultat) - len(conservees)


# ---------- avertissements ----------

def chercher_conflits_etiquettes(entrees: List[Entree]) -> Dict[str, List[Tuple[str, str]]]:
    """Repère les flexions qui portent plusieurs étiquettes différentes.

    :param entrees: entrées du lexique.
    :return: un dictionnaire ``{flexion: [(lemme, étiquettes), ...]}`` limité aux flexions concernées.
    """
    par_flexion = defaultdict(list)
    for flexion, lemme, etiquettes in entrees:
        par_flexion[flexion].append((lemme, etiquettes))
    return {f: v for f, v in par_flexion.items() if len({e for _, e in v}) > 1}


# ---------- construction du graphe (miroir de dawg.js) ----------

class Noeud:
    """Nœud d'un graphe acyclique de mots (DAWG) en cours de construction.

    :ivar numero: identifiant unique du nœud.
    :ivar final: ``True`` si un mot (ou son code de radical) se termine ici.
    :ivar arcs: arcs sortants, ``{valeur de l'arc: nœud suivant}``.
    :ivar adresse: adresse du nœud dans le graphe binaire.
    """

    compteur = 0
    """Compteur d'identifiants, remis à zéro pour chaque construction."""

    def __init__(self) -> None:
        """Crée un nœud vide avec un identifiant unique."""
        self.numero = Noeud.compteur
        Noeud.compteur += 1
        self.final = False
        self.arcs: Dict[int, "Noeud"] = {}
        self.adresse = 0

    def cle(self) -> str:
        """Clé d'équivalence du nœud, utilisée pour fusionner les nœuds identiques.

        Deux nœuds sont équivalents s'ils ont le même statut final et les mêmes arcs
        menant aux mêmes nœuds.

        :return: une chaîne qui identifie le contenu du nœud.
        """
        parties = ["1" if self.final else "0"]
        for valeur, noeud in self.arcs.items():
            parties += [str(valeur), str(noeud.numero)]
        return "_".join(parties)


def construire_dictionnaire(entrees: List[Entree], code_langue: str, nom_langue: str,
                            nom: str, description: str) -> Dict[str, Any]:
    """Compile des entrées en un dictionnaire Grammalecte (graphe de mots indexable).

    Chaque mot est codé ``[caractères..., code de radical, étiquettes]``, inséré dans un
    graphe acyclique minimal, puis converti en tableau d'entiers (``lByDic``) selon le
    format de Grammalecte. Les entrées doivent déjà être sans doublon.

    :param entrees: entrées ``(flexion, lemme, étiquettes)``.
    :param code_langue: code de langue (par exemple ``"fr"``).
    :param nom_langue: nom de la langue (par exemple ``"Français"``).
    :param nom: nom interne du dictionnaire.
    :param description: description du dictionnaire.
    :return: le dictionnaire, prêt à être écrit en JSON.
    """
    Noeud.compteur = 0
    caracteres, num_caractere, occ_caracteres = [""], {}, {}
    affixes, num_affixe, occ_affixes = [], {}, {}
    etiquettes, num_etiquette, occ_etiquettes = [], {}, {}
    bigrammes, bigrammes_vus, codees = [], set(), []
    for flexion, radical, etiquette in entrees:
        for i in range(len(flexion) - 1):
            bigramme = flexion[i:i + 2]
            if bigramme not in bigrammes_vus:
                bigrammes_vus.add(bigramme)
                bigrammes.append(bigramme)
        for c in flexion:
            if c not in num_caractere:
                num_caractere[c] = len(caracteres)
                caracteres.append(c)
            occ_caracteres[c] = occ_caracteres.get(c, 0) + 1
        code = definir_code_suffixe(flexion, radical)
        if code not in num_affixe:
            num_affixe[code] = len(affixes)
            affixes.append(code)
        occ_affixes[code] = occ_affixes.get(code, 0) + 1
        if etiquette not in num_etiquette:
            num_etiquette[etiquette] = len(etiquettes)
            etiquettes.append(etiquette)
        occ_etiquettes[etiquette] = occ_etiquettes.get(etiquette, 0) + 1
        codees.append((flexion, num_affixe[code], num_etiquette[etiquette]))

    nb_car, nb_aff = len(caracteres), len(affixes)
    valeurs_arcs = caracteres + affixes + etiquettes
    mots = [[num_caractere[c] for c in f] + [ia + nb_car, ie + nb_car + nb_aff] for f, ia, ie in codees]
    occurrences = {num_caractere[c]: occ_caracteres[c] for c in num_caractere}
    occurrences.update({v + nb_car: occ_affixes[a] for a, v in num_affixe.items()})
    occurrences.update({v + nb_car + nb_aff: occ_etiquettes[e] for e, v in num_etiquette.items()})
    mots.sort(key=lambda m: ",".join(map(str, m)))  # même ordre que Array.sort() de JavaScript

    racine, non_verifies, minimises, precedent = Noeud(), [], {}, []

    def minimiser(jusqu_a: int) -> None:
        """Fusionne les nœuds non vérifiés équivalents, de la feuille jusqu'à la profondeur ``jusqu_a``."""
        while len(non_verifies) > jusqu_a:
            parent, valeur, enfant = non_verifies.pop()
            cle = enfant.cle()
            if cle in minimises:
                parent.arcs[valeur] = minimises[cle]
            else:
                minimises[cle] = enfant

    for mot in mots:
        prefixe_commun = 0
        for i in range(min(len(mot), len(precedent))):
            if mot[i] != precedent[i]:
                break
            prefixe_commun += 1
        minimiser(prefixe_commun)
        noeud = racine if not non_verifies else non_verifies[-1][2]
        position = prefixe_commun
        for valeur in mot[prefixe_commun:]:
            suivant = Noeud()
            noeud.arcs[valeur] = suivant
            non_verifies.append((noeud, valeur, suivant))
            if position == len(mot) - 2:
                noeud.final = True
            position += 1
            noeud = suivant
        noeud.final = True
        precedent = mot
    minimiser(0)

    nb_arcs = len(racine.arcs) + sum(len(n.arcs) for n in minimises.values())
    for noeud in [racine] + list(minimises.values()):
        noeud.arcs = dict(sorted(noeud.arcs.items(), key=lambda couple: -occurrences[couple[0]]))
    nb_valeurs = len(valeurs_arcs)
    octets_arc = (nb_valeurs.bit_length() + 2) // 8 + 1
    octets_adresse = 1
    while (octets_arc + octets_adresse) * nb_arcs > 2 ** (octets_adresse * 8):
        octets_adresse += 1
    octets_noeud = octets_arc + octets_adresse
    adresse = len(racine.arcs) * octets_noeud
    for noeud in minimises.values():
        noeud.adresse = adresse
        adresse += max(len(noeud.arcs), 1) * octets_noeud
    masque_final, masque_dernier = 1 << (octets_arc * 8 - 1), 1 << (octets_arc * 8 - 2)
    graphe = []
    for noeud in [racine] + list(minimises.values()):
        if not noeud.arcs:
            graphe += [masque_final | masque_dernier, 0]
            continue
        total = len(noeud.arcs)
        for rang, (valeur, enfant) in enumerate(noeud.arcs.items(), 1):
            brut = valeur
            if rang == 1 and noeud.final:
                brut |= masque_final
            if rang == total:
                brut |= masque_dernier
            graphe += [brut, round(enfant.adresse / (octets_noeud / 2))]
    return {
        "sHeader": "/grammalecte-fsa/", "sLangCode": code_langue, "sLangName": nom_langue,
        "sDicName": nom, "sDescription": description, "sFileName": "[none]",
        "sDate": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "nEntry": len(mots), "nChar": nb_car - 1, "nAff": nb_aff,
        "nTag": nb_valeurs - (nb_car - 1) - nb_aff, "cStemming": "S", "dChar": num_caractere,
        "nNode": len(minimises), "nArc": nb_arcs, "lArcVal": valeurs_arcs, "nArcVal": nb_valeurs,
        "nBytesArc": octets_arc, "nBytesNodeAddress": octets_adresse, "nBytesOffset": 0,
        "lByDic": graphe, "l2grams": bigrammes,
    }


# ---------- programme ----------

def creer_analyseur() -> argparse.ArgumentParser:
    """Construit l'analyseur de la ligne de commande (options françaises, courtes et anglaises).

    :return: l'analyseur ``argparse`` configuré, sans l'option d'aide automatique
             (``-h``, ``--aide`` et ``--help`` sont déclarées à la main).
    """
    nom_defaut = "Dictionnaire_personnel_" + datetime.date.today().isoformat()
    analyseur = argparse.ArgumentParser(
        prog="grammalecte_fusion_dictionnaires.py",
        usage="%(prog)s [options] FICHIER [FICHIER ...]",
        description="Fusionne des vocabulaires .tsv et des dictionnaires .json Grammalecte en un dictionnaire compilé.",
        epilog="Sans paramètre, ce script affiche cette aide.",
        add_help=False,
    )
    analyseur._positionals.title = "arguments"
    analyseur._optionals.title = "options"
    analyseur.add_argument("fichiers", nargs="*", metavar="FICHIER",
                           help="fichiers .tsv (flexion, lemme, étiquettes) ou .json (dictionnaire compilé)")
    analyseur.add_argument("-n", "--nom", "--name", dest="nom", default=nom_defaut,
                           help=f"nom interne du dictionnaire (défaut : {nom_defaut})")
    analyseur.add_argument("-s", "-o", "--sortie", "--output", dest="sortie",
                           help="fichier de sortie (défaut : fr.<nom>.json)")
    analyseur.add_argument("-d", "--description", dest="description", default="",
                           help="description du dictionnaire")
    analyseur.add_argument("-l", "--langue", "--lang", dest="langue", default="fr",
                           help="code de la langue (défaut : fr)")
    analyseur.add_argument("-L", "--nom-langue", "--lang-name", dest="nom_langue", default="Français",
                           help="nom de la langue (défaut : Français)")
    analyseur.add_argument("-c", "--conserver-casse", "--keep-case", dest="conserver_casse", action="store_true",
                           help="ne pas fusionner les variantes de casse (Wazuh / wazuh / WAZUH)")
    analyseur.add_argument("-a", "--sans-avertissements", "--no-warnings", dest="sans_avertissements",
                           action="store_true", help="ne pas signaler les flexions à étiquettes multiples")
    analyseur.add_argument("-t", "--exporter-tsv", "--export-tsv", dest="exporter_tsv", metavar="FICHIER",
                           help="écrit aussi le lexique fusionné et trié en TSV")
    analyseur.add_argument("-h", "--aide", "--help", action="help", help="affiche cette aide et quitte")
    return analyseur


def main() -> None:
    """Point d'entrée : lit les fichiers, fusionne, compile et écrit le dictionnaire.

    Sans argument, affiche l'aide et quitte avec le code 0. Sinon : lit les ``.tsv`` et
    ``.json``, supprime les doublons exacts, fusionne la casse (sauf avec
    ``--conserver-casse``), trie sans tenir compte de la casse, compile, relit le
    résultat pour le vérifier, puis écrit le JSON (et le TSV si demandé).
    Les avertissements sur les étiquettes multiples vont sur la sortie d'erreur.

    :raises SystemExit: avec un message en cas de fichier introuvable ou illisible,
        d'extension non gérée, d'absence d'entrée ou d'erreur de vérification.
    """
    analyseur = creer_analyseur()
    if len(sys.argv) == 1:  # comportement par défaut : afficher l'aide
        analyseur.print_help()
        sys.exit(0)
    args = analyseur.parse_args()
    if not args.fichiers:
        analyseur.print_usage(sys.stderr)
        sys.exit("Erreur : au moins un fichier .tsv ou .json est requis.")

    fusion, par_fichier = [], []
    for nom_fichier in args.fichiers:
        chemin = Path(nom_fichier)
        if not chemin.is_file():
            sys.exit(f"Fichier introuvable : {chemin}")
        extension = chemin.suffix.lower()
        if extension == ".tsv":
            entrees = lire_tsv(chemin)
        elif extension == ".json":
            try:
                entrees = [(normaliser(f), normaliser(r), e) for f, r, e in lire_json(chemin)]
            except (ValueError, KeyError, json.JSONDecodeError) as erreur:
                sys.exit(f"{chemin} : illisible ({erreur})")
        else:
            sys.exit(f"Extension non gérée (.tsv ou .json attendu) : {chemin}")
        par_fichier.append((chemin.name, len(entrees)))
        fusion.extend(entrees)
    if not fusion:
        sys.exit("Aucune entrée à compiler.")

    uniques = list(dict.fromkeys(fusion))  # doublons exacts (flexion, lemme, étiquettes)
    nb_doublons_exacts = len(fusion) - len(uniques)
    nb_converties = nb_capitales = 0
    if not args.conserver_casse:
        uniques, nb_converties, nb_capitales = fusionner_casse(uniques)
    nb_doublons_casse = len(fusion) - nb_doublons_exacts - len(uniques)
    uniques.sort(key=lambda e: (e[0].casefold(), e[0], e[1].casefold(), e[1], e[2]))

    dico = construire_dictionnaire(uniques, args.langue, args.nom_langue, args.nom, args.description)

    relues = set(map(tuple, decoder_dictionnaire(dico)))  # contrôle : relecture du dictionnaire produit
    if relues != set(uniques):
        sys.exit("Erreur interne : la relecture du dictionnaire ne correspond pas aux entrées.")

    sortie = Path(args.sortie or f"{args.langue}.{args.nom}.json")
    with open(sortie, "w", encoding="utf-8") as fichier:
        json.dump(dico, fichier, ensure_ascii=False, separators=(",", ":"))
    if args.exporter_tsv:
        with open(args.exporter_tsv, "w", encoding="utf-8") as fichier:
            fichier.write("flexion\tlemme\tétiquettes\n")
            for entree in uniques:
                fichier.write("\t".join(entree) + "\n")

    for nom_fichier, nombre in par_fichier:
        print(f"  {nom_fichier} : {nombre} entrées")
    print(f"Total lu : {len(fusion)} | doublons exacts : {nb_doublons_exacts} | "
          f"doublons de casse fusionnés : {nb_doublons_casse} "
          f"(Titre->minuscules : {nb_converties}, CAPITALES supprimées : {nb_capitales}) | écrit : {len(uniques)}")
    print(f"-> {sortie} ({sortie.stat().st_size} octets, nom interne : {args.nom})")

    if not args.sans_avertissements:
        conflits = chercher_conflits_etiquettes(uniques)
        if conflits:
            print(f"\n[!] {len(conflits)} flexion(s) avec plusieurs étiquettes différentes (toutes conservées) :",
                  file=sys.stderr)
            for flexion in sorted(conflits, key=lambda s: (s.casefold(), s)):
                detail = ", ".join(f"{e} (lemme {l})" if l != flexion else e for l, e in conflits[flexion])
                print(f"    {flexion} : {detail}", file=sys.stderr)


if __name__ == "__main__":
    main()
