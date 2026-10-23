# Fusion de dictionnaires personnels pour Grammalecte

Script Python qui fusionne des vocabulaires (`.tsv`) et des dictionnaires compilés Grammalecte (`.json`) en un seul dictionnaire personnel à importer dans l’extension [Grammalecte](https://www.grammalecte.net/) pour Firefox.

Objectif : éviter les faux positifs de l’orthographe sur le vocabulaire technique (sysadmin, réseau, sécurité, DevOps, IA locale…) tout en gardant ses propres mots dans un fichier séparé.

## Pourquoi un script de fusion ?

L’éditeur lexical de l’extension Firefox n’expose qu’un seul dictionnaire personnel actif (`__personal__`). Le code de l’extension contient une gestion de « dictionnaires partagés », mais le bouton de création est commenté dans l’interface et le correcteur ne reçoit que le dictionnaire personnel. Pour avoir un vocabulaire technique d’un côté et ses mots à soi de l’autre, il faut donc les maintenir dans des fichiers séparés et les fusionner avant chaque import.

Le fichier exporté par Grammalecte est un graphe de mots compilé (format `/grammalecte-fsa/`), pas une liste lisible. Le script sait le relire et le recompiler.

## Utilisation rapide

```bash
python3 grammalecte_fusion_dictionnaires.py \
  "Vocabulaires/Vocabulaire sysadmin, netadmin, sécurité, DevOps, IA locale.tsv" \
  mes_mots.tsv \
  export_ancien_dictionnaire.json
```

Le script produit `fr.Dictionnaire_personnel_AAAA-MM-JJ.json` (date du jour), avec le même nom interne. Importe ensuite ce fichier dans l’éditeur lexical de Grammalecte.

> Attention : l’import remplace le dictionnaire personnel actuel. Exporte-le d’abord et passe-le au script comme l’un des fichiers d’entrée pour ne rien perdre.

Lancé sans paramètre, le script affiche son aide (comme avec `--aide`).

## Options

Chaque option existe en version courte (une lettre), en français et en anglais.

| Courte     | Française               | Anglaise        | Rôle                                                                       |
| ---------- | ----------------------- | --------------- | -------------------------------------------------------------------------- |
| `-n`       | `--nom`                 | `--name`        | Nom interne du dictionnaire (défaut : `Dictionnaire_personnel_AAAA-MM-JJ`) |
| `-s`, `-o` | `--sortie`              | `--output`      | Fichier de sortie (défaut : `fr.<nom>.json`)                               |
| `-d`       | `--description`         | `--description` | Description du dictionnaire                                                |
| `-l`       | `--langue`              | `--lang`        | Code de la langue (défaut : `fr`)                                          |
| `-L`       | `--nom-langue`          | `--lang-name`   | Nom de la langue (défaut : `Français`)                                     |
| `-c`       | `--conserver-casse`     | `--keep-case`   | Ne pas fusionner les variantes de casse                                    |
| `-a`       | `--sans-avertissements` | `--no-warnings` | Masquer les avertissements sur les étiquettes multiples                    |
| `-t`       | `--exporter-tsv`        | `--export-tsv`  | Écrire aussi le lexique fusionné et trié en TSV                            |
| `-h`       | `--aide`                | `--help`        | Afficher l’aide                                                            |

## Format des fichiers TSV

Trois colonnes séparées par des tabulations : flexion, lemme, étiquettes.

```text
flexion lemme étiquettes
wazuh wazuh :N:e:i/*
VLANs VLAN :N:e:i/*
PeerTube PeerTube :MP:m:i/*
```

- L’en-tête, les lignes vides et les lignes commençant par `#` sont ignorés.
- Les étiquettes commencent par `:`. Les plus courantes : `:N:e:i/*` (nom commun, épicène, invariable) et `:MP:e:i/*` (nom propre). Le genre « épicène » évite les fausses alertes d’accord sur les mots techniques (« un/une firewall »).
- Pour les verbes, laisse l’éditeur lexical de Grammalecte générer les formes conjuguées, puis exporte le dictionnaire et fournis-le au script en `.json`.

## Ce que fait la fusion

1. Lecture des `.tsv` et des `.json` (le script décode le graphe compilé). Les entrées sont normalisées : Unicode NFC et apostrophe typographique `’`, comme le fait Grammalecte avant une recherche.
2. Suppression des doublons exacts (même flexion, même lemme, mêmes étiquettes).
3. Fusion de la casse (désactivable avec `--conserver-casse`) :
   - une flexion en casse titre (`Wazuh`) devient minuscule (`wazuh`), car Grammalecte accepte alors `Wazuh` et `WAZUH` ;
   - une flexion en capitales (`WAZUH`) est supprimée si sa version minuscule existe ;
   - les graphies mixtes (`OpenWrt`, `macOS`, `PeerTube`) sont conservées.
4. Tri sans tenir compte de la casse.
5. Compilation du dictionnaire, puis relecture de contrôle : le script relit le fichier produit et vérifie qu’il contient exactement les entrées prévues.
6. Avertissements (sur la sortie d’erreur) pour les flexions qui portent plusieurs étiquettes différentes, par exemple `wazuh` en nom propre et en nom commun après la fusion de la casse. Toutes les étiquettes sont conservées ; à toi de trancher. Redirige avec `2> avertissements.txt` si besoin.

## Limites connues

- Le vocabulaire fourni ne contient que quelques infinitifs et participes de verbes de jargon (pinguer, flasher…). Les formes conjuguées ne sont pas listées.

## Documentation du code

Le module est documenté via pydoc avec des docstrings et des annotations de types :

```bash
python3 -m pydoc grammalecte_fusion_dictionnaires        # dans le terminal
python3 -m pydoc -w grammalecte_fusion_dictionnaires     # page HTML
```

## Références

- [Grammalecte](https://www.grammalecte.net/) : correcteur grammatical et typographique libre pour le français.
- Dépôt de développement actuel : [algoo/grammalecte](https://github.com/algoo/grammalecte). Les algorithmes de construction et de lecture du graphe sont portés de `graphspell-js/dawg.js`, `ibdawg.js` et `str_transform.js`.
