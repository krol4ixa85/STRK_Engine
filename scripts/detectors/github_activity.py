#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
github_activity.py — Starknet dev activity tracker

Отслеживает GitHub metrics для starkware-libs org:
  · Commits per day (velocity)
  · Open/closed PRs
  · Recent releases (major/minor)
  · Contributor count
  · Repository stars trend

Ключевые repos:
  · starkware-libs/cairo — main language
  · starkware-libs/blockifier — sequencer
  · starkware-libs/sequencer — L2 sequencer
  · eqlabs/pathfinder — full node
  · madara-alliance/madara — L3 stack

Signals:
  · SPIKE_DEV_ACTIVITY: commits/day растут 2× vs baseline → скоро big announcement/upgrade
  · NEW_RELEASE_MAJOR: v0.x.0 → скорее всего price catalyst
  · DEV_SLOWDOWN: activity падает > 50% → concern
  · STABLE: normal cadence

Rate limit: без key 60/hour, с key 5000. Ты можешь добавить GITHUB_TOKEN.
"""

import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'github_activity.json'

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')

# Key Starknet ecosystem repos
REPOS = [
    {'org': 'starkware-libs', 'repo': 'cairo', 'weight': 3, 'kind': 'language'},
    {'org': 'starkware-libs', 'repo': 'sequencer', 'weight': 3, 'kind': 'core'},
    {'org': 'starkware-libs', 'repo': 'blockifier', 'weight': 2, 'kind': 'core'},
    {'org': 'starkware-libs', 'repo': 'stone-prover', 'weight': 2, 'kind': 'prover'},
    {'org': 'starkware-libs', 'repo': 'starknet-p2p-specs', 'weight': 1, 'kind': 'spec'},
    {'org': 'eqlabs', 'repo': 'pathfinder', 'weight': 2, 'kind': 'node'},
    {'org': 'madara-alliance', 'repo': 'madara', 'weight': 1, 'kind': 'appchain'},
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('gh')


def api_call(url, timeout=15):
    headers = {'User-Agent': 'STRK-Engine/1.0', 'Accept': 'application/vnd.github+json'}
    if GITHUB_TOKEN:
        headers['Authorization'] = f'Bearer {GITHUB_TOKEN}'
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.warning(f"Rate limited: {e}")
        else:
            logger.error(f"HTTP {e.code}: {url}")
        return None
    except Exception as e:
        logger.error(f"Error: {e}")
        return None


def fetch_repo_stats(org, repo, days_back=14):
    """Get commits, PRs, releases for a repo."""
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    
    # Commits
    commits_url = f'https://api.github.com/repos/{org}/{repo}/commits?since={since}&per_page=100'
    commits = api_call(commits_url) or []
    
    # PRs (open + recently closed)
    prs_url = f'https://api.github.com/repos/{org}/{repo}/pulls?state=all&per_page=50&sort=updated'
    prs = api_call(prs_url) or []
    
    # Filter PRs to window
    recent_prs = []
    for pr in prs:
        try:
            updated = datetime.fromisoformat(pr['updated_at'].replace('Z', '+00:00'))
            if updated > datetime.now(timezone.utc) - timedelta(days=days_back):
                recent_prs.append(pr)
        except (KeyError, ValueError):
            continue
    
    # Releases
    releases_url = f'https://api.github.com/repos/{org}/{repo}/releases?per_page=5'
    releases = api_call(releases_url) or []
    
    recent_releases = []
    for r in releases[:5]:
        try:
            published = datetime.fromisoformat(r['published_at'].replace('Z', '+00:00'))
            if published > datetime.now(timezone.utc) - timedelta(days=days_back):
                recent_releases.append({
                    'tag': r.get('tag_name'),
                    'name': r.get('name'),
                    'published_at': r.get('published_at'),
                    'prerelease': r.get('prerelease', False),
                })
        except (KeyError, ValueError):
            continue
    
    # Commit velocity per day
    commits_by_day = defaultdict(int)
    unique_authors = set()
    for c in commits:
        try:
            date_str = c['commit']['committer']['date']
            day = date_str[:10]
            commits_by_day[day] += 1
            author = c.get('author', {}) or {}
            if author.get('login'):
                unique_authors.add(author['login'])
        except (KeyError, TypeError):
            continue
    
    # Recent commits (last 3)
    recent_commits = []
    for c in commits[:3]:
        try:
            recent_commits.append({
                'sha': c['sha'][:7],
                'message': c['commit']['message'].split('\n')[0][:100],
                'author': (c.get('author') or {}).get('login', 'unknown'),
                'date': c['commit']['committer']['date'],
            })
        except (KeyError, TypeError):
            continue
    
    return {
        'org': org,
        'repo': repo,
        'commits_count': len(commits),
        'unique_authors': len(unique_authors),
        'prs_open': sum(1 for p in recent_prs if p.get('state') == 'open'),
        'prs_closed': sum(1 for p in recent_prs if p.get('state') == 'closed'),
        'releases_count': len(recent_releases),
        'recent_releases': recent_releases,
        'commits_per_day_avg': round(len(commits) / days_back, 2),
        'commits_by_day': dict(commits_by_day),
        'recent_commits': recent_commits,
    }


def classify_activity(stats_by_repo):
    """Classify overall dev activity level."""
    total_commits = sum(s['commits_count'] for s in stats_by_repo)
    total_authors = sum(s['unique_authors'] for s in stats_by_repo)
    total_prs_open = sum(s['prs_open'] for s in stats_by_repo)
    total_prs_closed = sum(s['prs_closed'] for s in stats_by_repo)
    total_releases = sum(s['releases_count'] for s in stats_by_repo)
    
    # Weighted score
    activity_score = 0
    for s in stats_by_repo:
        # Find weight
        weight = 1
        for r in REPOS:
            if r['org'] == s['org'] and r['repo'] == s['repo']:
                weight = r['weight']
                break
        activity_score += s['commits_count'] * weight
    
    # Get major releases (non-prerelease)
    major_releases = []
    for s in stats_by_repo:
        for r in s['recent_releases']:
            if not r.get('prerelease'):
                major_releases.append({
                    'repo': f"{s['org']}/{s['repo']}",
                    'tag': r['tag'],
                    'name': r['name'],
                    'date': r['published_at'][:10],
                })
    
    # === CLASSIFICATION ===
    # STRK dev baseline (rough estimate): ~50-80 commits/week across ecosystem
    # Normalized to 14 days: 100-160
    
    signal = 'NORMAL'
    interpretation = ''
    
    if activity_score > 400 and total_releases >= 2:
        signal = 'HIGH_ACTIVITY_WITH_RELEASES'
        interpretation = f'Very active dev + {total_releases} releases in 14d — potential upgrade catalyst'
    elif activity_score > 400:
        signal = 'HIGH_ACTIVITY'
        interpretation = f'{total_commits} commits/14d, {total_authors} authors — high momentum'
    elif activity_score > 200:
        signal = 'NORMAL_ACTIVITY'
        interpretation = f'{total_commits} commits/14d — normal pace'
    elif activity_score > 100:
        signal = 'LOW_ACTIVITY'
        interpretation = f'{total_commits} commits/14d — slower than usual'
    else:
        signal = 'VERY_LOW'
        interpretation = f'{total_commits} commits/14d — concerning slowdown'
    
    if major_releases:
        interpretation += f'\nMajor releases: {", ".join(f"{r["repo"]} {r["tag"]}" for r in major_releases[:3])}'
    
    return {
        'signal': signal,
        'interpretation': interpretation,
        'activity_score': activity_score,
        'total_commits_14d': total_commits,
        'total_authors_14d': total_authors,
        'total_prs_open': total_prs_open,
        'total_prs_closed': total_prs_closed,
        'total_releases_14d': total_releases,
        'major_releases': major_releases,
    }


def main():
    logger.info("=" * 60)
    logger.info("GITHUB DEV ACTIVITY · Starknet ecosystem")
    logger.info("=" * 60)
    
    stats_by_repo = []
    
    for repo_info in REPOS:
        logger.info(f"\n  Fetching {repo_info['org']}/{repo_info['repo']}...")
        stats = fetch_repo_stats(repo_info['org'], repo_info['repo'], days_back=14)
        if stats:
            stats_by_repo.append(stats)
            logger.info(f"    Commits: {stats['commits_count']}, Authors: {stats['unique_authors']}")
            logger.info(f"    PRs open: {stats['prs_open']}, closed: {stats['prs_closed']}")
            if stats['recent_releases']:
                for r in stats['recent_releases']:
                    prefix = "⚡" if not r.get('prerelease') else "🔧"
                    logger.info(f"    {prefix} Release: {r['tag']} · {r['published_at'][:10]}")
    
    # Classify
    classification = classify_activity(stats_by_repo)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"OVERALL: {classification['signal']}")
    logger.info(f"{classification['interpretation']}")
    logger.info(f"Total: {classification['total_commits_14d']} commits, {classification['total_authors_14d']} authors")
    logger.info(f"Weighted activity score: {classification['activity_score']}")
    
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'classification': classification,
        'repos': stats_by_repo,
    }
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
