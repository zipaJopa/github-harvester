#!/usr/bin/env python3
"""GitHub Project Harvester - Auto-discover valuable projects"""
import requests
import json
import time
from datetime import datetime

class GitHubHarvester:
    def __init__(self, github_token):
        self.token = github_token
        self.headers = {'Authorization': f'token {github_token}'}
        
    def harvest_trending_projects(self):
        print("⚡ Harvesting trending GitHub projects...")
        topics = ['ai-agent', 'automation', 'saas-template', 'trading-bot']
        
        for topic in topics:
            repos = self.search_repos_by_topic(topic)
            for repo in repos[:3]:
                self.analyze_and_store_project(repo)
    
    def search_repos_by_topic(self, topic):
        url = "https://api.github.com/search/repositories"
        params = {
            'q': f'topic:{topic} stars:>10 created:>2024-01-01',
            'sort': 'stars',
            'order': 'desc',
            'per_page': 10
        }
        
        response = requests.get(url, params=params, headers=self.headers)
        if response.status_code == 200:
            return response.json().get('items', [])
        return []
    
    def analyze_and_store_project(self, repo):
        analysis = {
            'url': repo['html_url'],
            'stars': repo['stargazers_count'],
            'description': repo['description'],
            'language': repo['language'],
            'topics': repo.get('topics', []),
            'analyzed_at': datetime.now().isoformat(),
            'value_score': self.calculate_value_score(repo)
        }
        
        print(f"📦 Harvested: {repo['name']} (Value: {analysis['value_score']})")
    
    def calculate_value_score(self, repo):
        score = 0
        score += min(repo['stargazers_count'] / 10, 50)
        
        high_value_topics = ['ai', 'automation', 'saas', 'api', 'bot']
        topics = repo.get('topics', []) + [repo.get('description', '')]
        for topic in high_value_topics:
            if any(topic in str(t).lower() for t in topics):
                score += 20
        
        return min(score, 100)

if __name__ == "__main__":
    import os
    harvester = GitHubHarvester(os.getenv('GITHUB_TOKEN'))
    harvester.harvest_trending_projects()
