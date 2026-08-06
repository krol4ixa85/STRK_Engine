#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_registry.py — Управление списком отслеживаемых кошельков

Динамически добавляй/убирай адреса для мониторинга.
Обновляет flow_seeds.json и label mappings в whale_monitor.

Usage:
    python3 wallet_registry.py list                              # показать всё
    python3 wallet_registry.py list --category cex               # только CEX
    python3 wallet_registry.py add <addr> <name> <category> [--role "..."] [--chain ethereum|starknet]
    python3 wallet_registry.py remove <addr_or_name>
    python3 wallet_registry.py note <addr> "new note"           # обновить note
    python3 wallet_registry.py import <file.csv>                # массовый импорт
    python3 wallet_registry.py export <file.csv>                # экспорт
    python3 wallet_registry.py search <substring>                # найти по адресу/имени

Categories (для правильной классификации в whale_monitor):
    l1_infrastructure   · L1 bridges, contracts
    custody_and_transit · custody wallets, transit bridgers
    l2_native          · Starknet L2 addresses
    cex_hot_wallets    · exchange hot wallets  
    team_and_foundation · team multisigs, foundation
    watchlist          · other addresses of interest (default)

Interactive mode:
    python3 wallet_registry.py                                   # меню
"""

import os
import sys
import json
import csv
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
LABELS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'address_labels.json'
BACKUP_DIR = SCRIPT_DIR / 'data' / 'seeds' / 'backups'
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

VALID_CATEGORIES = [
    'l1_infrastructure',
    'custody_and_transit',
    'l2_native',
    'cex_hot_wallets_known_dynamic',
    'team_and_foundation',
    'watchlist',
]


def load_seeds():
    if not SEEDS_FILE.exists():
        return {'_meta': {'created': datetime.now(timezone.utc).isoformat()}}
    with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_seeds(seeds):
    # Backup previous
    if SEEDS_FILE.exists():
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        backup = BACKUP_DIR / f'flow_seeds_{ts}.json'
        with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
            with open(backup, 'w', encoding='utf-8') as bf:
                bf.write(f.read())
    
    seeds.setdefault('_meta', {})
    seeds['_meta']['last_modified'] = datetime.now(timezone.utc).isoformat()
    
    with open(SEEDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(seeds, f, indent=2, ensure_ascii=False)


def normalize_address(addr, chain='ethereum'):
    """Normalize address format."""
    addr = addr.strip().lower()
    if not addr.startswith('0x'):
        addr = '0x' + addr
    
    if chain == 'ethereum':
        # Ethereum: exactly 42 chars (0x + 40 hex)
        if len(addr) != 42:
            raise ValueError(f"Invalid ETH address length: {len(addr)}, expected 42")
        if not re.match(r'^0x[a-f0-9]{40}$', addr):
            raise ValueError(f"Invalid ETH address: {addr}")
    else:
        # Starknet: up to 66 chars, strip leading zeros
        hex_part = addr[2:].lstrip('0') or '0'
        if len(hex_part) > 64:
            raise ValueError(f"Invalid Starknet address (too long)")
        addr = '0x' + hex_part
    
    return addr


def cmd_list(args, seeds):
    """List all wallets, optionally filtered by category."""
    print(f"\n{'='*100}")
    print(f"{'CATEGORY':<32} {'NAME':<32} {'ADDRESS':<44} {'ROLE'}")
    print(f"{'-'*130}")
    
    SKIP = {'_meta', '_phantoms'}
    count = 0
    for cat, data in sorted(seeds.items()):
        if cat in SKIP or not isinstance(data, dict):
            continue
        if args.category and args.category != cat:
            continue
        for name, entry in sorted(data.items()):
            if name.startswith('_') or not isinstance(entry, dict):
                continue
            addr = entry.get('address', 'N/A')
            role = entry.get('role', '')[:40]
            if len(role) > 40:
                role = role[:37] + '...'
            print(f"{cat:<32} {name:<32} {addr:<44} {role}")
            count += 1
    
    print(f"\nTotal: {count} wallets")


def cmd_add(args, seeds):
    """Add new wallet to registry."""
    try:
        addr = normalize_address(args.address, args.chain)
    except ValueError as e:
        print(f"[FAIL] {e}")
        return 1
    
    category = args.category
    if category not in VALID_CATEGORIES:
        print(f"[FAIL] Invalid category. Valid: {', '.join(VALID_CATEGORIES)}")
        return 1
    
    name = args.name.strip().replace(' ', '_').lower()
    
    # Check if already exists
    for cat, data in seeds.items():
        if isinstance(data, dict):
            for existing_name, entry in data.items():
                if isinstance(entry, dict):
                    if entry.get('address', '').lower() == addr:
                        print(f"[WARN] Address already exists as '{existing_name}' in category '{cat}'")
                        confirm = input("Overwrite? (y/N): ").strip().lower()
                        if confirm != 'y':
                            return 1
                        del data[existing_name]
    
    # Check name conflict in target category
    seeds.setdefault(category, {})
    if name in seeds[category]:
        print(f"[WARN] Name '{name}' already exists in category '{category}'")
        confirm = input("Overwrite? (y/N): ").strip().lower()
        if confirm != 'y':
            return 1
    
    entry = {
        'address': addr,
        'added': datetime.now(timezone.utc).isoformat(),
    }
    if args.role:
        entry['role'] = args.role
    if args.importance:
        entry['importance'] = args.importance
    if args.note:
        entry['note'] = args.note
    
    seeds[category][name] = entry
    save_seeds(seeds)
    
    print(f"[OK] Added {name} ({addr}) to {category}")
    if args.role:
        print(f"     Role: {args.role}")
    return 0


def cmd_remove(args, seeds):
    """Remove wallet by name or address."""
    query = args.identifier.strip().lower()
    if query.startswith('0x'):
        try:
            query = normalize_address(query, 'ethereum')
        except ValueError:
            pass
    
    found = []
    for cat, data in seeds.items():
        if not isinstance(data, dict):
            continue
        for name, entry in data.items():
            if not isinstance(entry, dict):
                continue
            if name.lower() == query or entry.get('address', '').lower() == query:
                found.append((cat, name, entry))
    
    if not found:
        print(f"[FAIL] Not found: {query}")
        return 1
    
    for cat, name, entry in found:
        print(f"Found in '{cat}': {name} → {entry.get('address')}")
    
    if len(found) > 1:
        print(f"[WARN] Multiple matches. Specify address to be exact.")
        return 1
    
    cat, name, entry = found[0]
    if not getattr(args, 'yes', False):
        confirm = input(f"Remove '{name}' from '{cat}'? (y/N): ").strip().lower()
        if confirm != 'y':
            print("Cancelled")
            return 0
    
    del seeds[cat][name]
    save_seeds(seeds)
    print(f"[OK] Removed {name}")
    return 0


def cmd_note(args, seeds):
    """Add/update note on wallet."""
    query = args.identifier.strip().lower()
    
    found = None
    for cat, data in seeds.items():
        if not isinstance(data, dict):
            continue
        for name, entry in data.items():
            if not isinstance(entry, dict):
                continue
            if name.lower() == query or entry.get('address', '').lower() == query:
                found = (cat, name)
                break
        if found:
            break
    
    if not found:
        print(f"[FAIL] Not found: {query}")
        return 1
    
    cat, name = found
    seeds[cat][name]['note'] = args.note
    seeds[cat][name]['note_updated'] = datetime.now(timezone.utc).isoformat()
    save_seeds(seeds)
    print(f"[OK] Updated note for {name}")
    return 0


def cmd_search(args, seeds):
    """Search wallets by substring in name/address/role."""
    query = args.query.lower()
    print(f"\nSearching for '{query}'...")
    print(f"{'CATEGORY':<32} {'NAME':<32} {'ADDRESS':<44} {'ROLE'}")
    print(f"{'-'*130}")
    
    count = 0
    for cat, data in seeds.items():
        if not isinstance(data, dict):
            continue
        for name, entry in data.items():
            if not isinstance(entry, dict):
                continue
            hay = f"{name} {entry.get('address', '')} {entry.get('role', '')} {entry.get('note', '')}".lower()
            if query in hay:
                role = (entry.get('role', '') or '')[:40]
                print(f"{cat:<32} {name:<32} {entry.get('address', 'N/A'):<44} {role}")
                count += 1
    
    print(f"\nMatches: {count}")


def cmd_import(args, seeds):
    """Bulk import from CSV.
    Format: address,name,category[,role,importance,note,chain]
    """
    if not Path(args.file).exists():
        print(f"[FAIL] File not found: {args.file}")
        return 1
    
    added = 0
    skipped = 0
    with open(args.file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                chain = row.get('chain', 'ethereum').strip().lower() or 'ethereum'
                addr = normalize_address(row['address'], chain)
                cat = row['category'].strip()
                name = row['name'].strip().replace(' ', '_').lower()
                
                if cat not in VALID_CATEGORIES:
                    print(f"[SKIP] {name}: invalid category '{cat}'")
                    skipped += 1
                    continue
                
                entry = {'address': addr, 'added': datetime.now(timezone.utc).isoformat()}
                if row.get('role'):
                    entry['role'] = row['role'].strip()
                if row.get('importance'):
                    entry['importance'] = row['importance'].strip()
                if row.get('note'):
                    entry['note'] = row['note'].strip()
                
                seeds.setdefault(cat, {})
                seeds[cat][name] = entry
                added += 1
                print(f"[OK] {cat}/{name}")
            except (KeyError, ValueError) as e:
                skipped += 1
                print(f"[SKIP] {row}: {e}")
    
    if added > 0:
        save_seeds(seeds)
    print(f"\n[DONE] Added: {added}, Skipped: {skipped}")
    return 0


def cmd_export(args, seeds):
    """Export all wallets to CSV."""
    with open(args.file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'address', 'name', 'category', 'chain', 'role', 'importance', 'note', 'added'
        ])
        writer.writeheader()
        
        count = 0
        for cat, data in seeds.items():
            if not isinstance(data, dict):
                continue
            for name, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                chain = 'starknet' if cat == 'l2_native' else 'ethereum'
                writer.writerow({
                    'address': entry.get('address', ''),
                    'name': name,
                    'category': cat,
                    'chain': chain,
                    'role': entry.get('role', ''),
                    'importance': entry.get('importance', ''),
                    'note': entry.get('note', ''),
                    'added': entry.get('added', ''),
                })
                count += 1
    
    print(f"[OK] Exported {count} wallets to {args.file}")
    return 0


def interactive_menu(seeds):
    """Interactive menu for non-CLI usage."""
    print("\n" + "="*60)
    print("STRK Engine · Wallet Registry Manager")
    print("="*60)
    while True:
        print("""
1. List all wallets
2. List by category
3. Search
4. Add wallet
5. Remove wallet
6. Update note
7. Export to CSV
8. Import from CSV
9. Exit
""")
        choice = input("Choose (1-9): ").strip()
        
        if choice == '1':
            args = argparse.Namespace(category=None)
            cmd_list(args, seeds)
        elif choice == '2':
            print(f"Categories: {', '.join(VALID_CATEGORIES)}")
            cat = input("Category: ").strip()
            args = argparse.Namespace(category=cat)
            cmd_list(args, seeds)
        elif choice == '3':
            query = input("Search: ").strip()
            args = argparse.Namespace(query=query)
            cmd_search(args, seeds)
        elif choice == '4':
            addr = input("Address (0x...): ").strip()
            name = input("Name (short label): ").strip()
            print(f"Categories: {', '.join(VALID_CATEGORIES)}")
            cat = input("Category: ").strip()
            role = input("Role (optional): ").strip()
            importance = input("Importance (critical/high/medium/low, optional): ").strip()
            note = input("Note (optional): ").strip()
            chain = input("Chain (ethereum/starknet, default ethereum): ").strip() or 'ethereum'
            
            args = argparse.Namespace(
                address=addr, name=name, category=cat,
                role=role or None, importance=importance or None,
                note=note or None, chain=chain,
            )
            cmd_add(args, seeds)
            seeds = load_seeds()  # reload to get saved state
        elif choice == '5':
            ident = input("Address or name to remove: ").strip()
            args = argparse.Namespace(identifier=ident)
            cmd_remove(args, seeds)
            seeds = load_seeds()
        elif choice == '6':
            ident = input("Address or name: ").strip()
            note = input("New note: ").strip()
            args = argparse.Namespace(identifier=ident, note=note)
            cmd_note(args, seeds)
            seeds = load_seeds()
        elif choice == '7':
            fp = input("Export path (default wallets_export.csv): ").strip() or 'wallets_export.csv'
            args = argparse.Namespace(file=fp)
            cmd_export(args, seeds)
        elif choice == '8':
            fp = input("Import CSV path: ").strip()
            args = argparse.Namespace(file=fp)
            cmd_import(args, seeds)
            seeds = load_seeds()
        elif choice == '9':
            print("Bye!")
            break
        else:
            print("Invalid choice")


def main():
    parser = argparse.ArgumentParser(description='STRK Wallet Registry Manager')
    subs = parser.add_subparsers(dest='cmd')
    
    p_list = subs.add_parser('list', help='List wallets')
    p_list.add_argument('--category', help='Filter by category')
    
    p_add = subs.add_parser('add', help='Add wallet')
    p_add.add_argument('address', help='0x...')
    p_add.add_argument('name', help='Short label')
    p_add.add_argument('category', help='Category', choices=VALID_CATEGORIES)
    p_add.add_argument('--role', help='Role description')
    p_add.add_argument('--importance', help='critical/high/medium/low')
    p_add.add_argument('--note', help='Additional note')
    p_add.add_argument('--chain', default='ethereum', choices=['ethereum', 'starknet'])
    
    p_rm = subs.add_parser('remove', help='Remove wallet')
    p_rm.add_argument('identifier', help='Address or name')
    p_rm.add_argument('--yes', action='store_true', help='Skip confirmation')
    
    p_note = subs.add_parser('note', help='Update note')
    p_note.add_argument('identifier', help='Address or name')
    p_note.add_argument('note', help='New note text')
    
    p_search = subs.add_parser('search', help='Search wallets')
    p_search.add_argument('query', help='Substring to search')
    
    p_imp = subs.add_parser('import', help='Import CSV')
    p_imp.add_argument('file', help='CSV file path')
    
    p_exp = subs.add_parser('export', help='Export CSV')
    p_exp.add_argument('file', help='CSV file path')
    
    args = parser.parse_args()
    seeds = load_seeds()
    
    if not args.cmd:
        interactive_menu(seeds)
        return 0
    
    handlers = {
        'list': cmd_list,
        'add': cmd_add,
        'remove': cmd_remove,
        'note': cmd_note,
        'search': cmd_search,
        'import': cmd_import,
        'export': cmd_export,
    }
    
    return handlers[args.cmd](args, seeds)


if __name__ == '__main__':
    sys.exit(main())
