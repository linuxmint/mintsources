#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Software Sources MythicOS
Gère les dépôts APT :
- Dépôts Ubuntu de base
- Dépôts Linux Mint de base
- Dépôt MythicOS personnalisé
"""

import os
import subprocess
import sys

SOURCES_LIST_DIR = "/etc/apt/sources.list.d/"

# Dépôt MythicOS
MYTHICOS_REPO = "deb [signed-by=/usr/share/keyrings/mythicos.gpg] https://packages.mythicos.hastag.fr/ stable main"
MYTHICOS_LIST_FILE = os.path.join(SOURCES_LIST_DIR, "mythicos.list")

# Dépôts Ubuntu et Mint génériques
BASE_REPOS = [
    # Ubuntu main + updates + security
    "deb http://archive.ubuntu.com/ubuntu focal main restricted universe multiverse",
    "deb http://archive.ubuntu.com/ubuntu focal-updates main restricted universe multiverse",
    "deb http://archive.ubuntu.com/ubuntu focal-security main restricted universe multiverse",
    # Linux Mint base (générique)
    "deb http://packages.linuxmint.com ulyssa main upstream import backport"
]

def check_root():
    """Vérifie si le script est exécuté avec sudo/root"""
    if os.geteuid() != 0:
        print("Ce programme doit être exécuté avec sudo.")
        sys.exit(1)

def add_repo(repo_line, filename):
    """Ajoute un dépôt dans un fichier si ce n'est pas déjà présent"""
    if os.path.exists(filename):
        return
    try:
        with open(filename, "w") as f:
            f.write(repo_line + "\n")
        print(f"Dépôt ajouté : {filename}")
    except Exception as e:
        print(f"Erreur lors de l’ajout du dépôt {repo_line} : {e}")

def add_base_repos():
    """Ajoute tous les dépôts Ubuntu et Mint"""
    for i, repo in enumerate(BASE_REPOS):
        file_path = os.path.join(SOURCES_LIST_DIR, f"base-{i+1}.list")
        add_repo(repo, file_path)

def add_mythicos_repo():
    """Ajoute le dépôt MythicOS"""
    add_repo(MYTHICOS_REPO, MYTHICOS_LIST_FILE)

def update_sources():
    """Met à jour la liste des paquets APT"""
    try:
        subprocess.run(["apt", "update"], check=True)
        print("Liste des paquets mise à jour.")
    except subprocess.CalledProcessError:
        print("Erreur lors de la mise à jour des paquets.")
        sys.exit(1)

def main():
    check_root()
    add_base_repos()     # Ubuntu + Mint
    add_mythicos_repo()  # MythicOS
    update_sources()
    print("Software Sources MythicOS terminé.")

if __name__ == "__main__":
    main()
