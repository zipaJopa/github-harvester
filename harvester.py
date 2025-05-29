#!/usr/bin/env python3
"""
GitHub Project Harvester - GitHub-native Agent
---------------------------------------------
Harvests valuable GitHub projects based on:
1. Scheduled runs (every 2 hours)
2. Task assignments from agent-controller

Part of the AI Constellation system.
"""
import requests
import json
import time
import os
import base64
from datetime import datetime

# Configuration Constants
AGENT_TASKS_REPO = "zipaJopa/agent-tasks"
AGENT_RESULTS_REPO = "zipaJopa/agent-results"
HARVESTED_DIR = "harvested"
OUTPUTS_DIR = "outputs"

class GitHubAPI:
    """Helper class for GitHub API interactions with proper rate limiting and error handling"""
    
    def __init__(self, token):
        self.token = token
        self.headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
    
    def _request(self, method, url, params=None, data=None, max_retries=3):
        """Make a GitHub API request with automatic rate limit handling and retries"""
        for attempt in range(max_retries):
            try:
                response = self.session.request(method, url, params=params, json=data)
                
                # Check for rate limiting
                if response.status_code == 403 and 'X-RateLimit-Remaining' in response.headers and int(response.headers['X-RateLimit-Remaining']) == 0:
                    reset_time = int(response.headers.get('X-RateLimit-Reset', time.time() + 60))
                    sleep_duration = max(reset_time - time.time() + 1, 1)
                    print(f"Rate limited. Sleeping for {sleep_duration:.1f} seconds...")
                    time.sleep(sleep_duration)
                    continue
                    
                # Success
                if response.status_code in (200, 201, 204):
                    return response.json() if response.content else {}
                    
                # Not found
                if response.status_code == 404:
                    print(f"Resource not found: {url}")
                    return None
                    
                # Other errors
                print(f"GitHub API error: {response.status_code} - {response.text}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                    
                response.raise_for_status()
                
            except requests.RequestException as e:
                print(f"Request error: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
                
        return None
    
    def get_harvest_tasks(self, repo, label="in-progress"):
        """Get harvest tasks (issues) with a specific label"""
        url = f"https://api.github.com/repos/{repo}/issues"
        params = {
            'state': 'open',
            'labels': label
        }
        issues = self._request('GET', url, params=params) or []
        
        # Filter for harvest tasks by checking title and body
        harvest_tasks = []
        for issue in issues:
            title = issue.get('title', '').lower()
            body = issue.get('body', '')
            
            # Check if it's a harvest task by title
            if 'harvest' in title:
                harvest_tasks.append(issue)
                continue
                
            # Or check the task type in the JSON body
            try:
                task_json = json.loads(body)
                if task_json.get('type', '').lower() == 'harvest':
                    harvest_tasks.append(issue)
            except (json.JSONDecodeError, AttributeError):
                # Not a valid JSON or doesn't have type field
                pass
                
        return harvest_tasks
    
    def get_issue_body(self, repo, issue_number):
        """Get the body content of a specific issue"""
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
        issue_data = self._request('GET', url)
        return issue_data.get('body', '') if issue_data else ''
    
    def create_comment(self, repo, issue_number, comment_text):
        """Create a comment on an issue"""
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
        data = {'body': comment_text}
        return self._request('POST', url, data=data)
    
    def close_issue(self, repo, issue_number):
        """Close an issue"""
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
        data = {'state': 'closed'}
        return self._request('PATCH', url, data=data)
    
    def create_or_update_file(self, repo, path, content, message, sha=None):
        """Create or update a file in a repository"""
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        
        # Check if file exists to get SHA
        if sha is None:
            existing = self._request('GET', url)
            if existing and 'sha' in existing:
                sha = existing['sha']
        
        # Prepare the content and request
        content_bytes = content.encode('utf-8') if isinstance(content, str) else content
        content_b64 = base64.b64encode(content_bytes).decode('utf-8')
        
        data = {
            'message': message,
            'content': content_b64
        }
        
        if sha:
            data['sha'] = sha
            
        return self._request('PUT', url, data=data)

class GitHubHarvester:
    def __init__(self, github_token):
        self.token = github_token
        self.github = GitHubAPI(github_token)
        self.harvested_projects = []
        
    def run(self):
        """Main entry point - check for assigned tasks and/or run scheduled harvest"""
        # Check if this is a scheduled run or manual trigger
        if os.environ.get('SCHEDULED_RUN') == 'true':
            print("Running scheduled full harvest...")
            self.run_scheduled_harvest()
        
        # Always check for assigned tasks
        print("Checking for harvest tasks in agent-tasks repository...")
        self.process_harvest_tasks()
    
    def run_scheduled_harvest(self):
        """Run the regular scheduled harvest of trending projects"""
        print("Running scheduled harvest...")
        self.harvest_trending_projects()
        
        # Save results locally
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result_file = f"{HARVESTED_DIR}/harvest_{timestamp}.json"
        
        os.makedirs(HARVESTED_DIR, exist_ok=True)
        with open(result_file, 'w') as f:
            json.dump(self.harvested_projects, f, indent=2)
            
        print(f"Harvest completed. Found {len(self.harvested_projects)} projects. Results saved to {result_file}")
    
    def process_harvest_tasks(self):
        """Process harvest tasks from the agent-tasks repository"""
        tasks = self.github.get_harvest_tasks(AGENT_TASKS_REPO)
        
        if not tasks:
            print("No harvest tasks found with 'in-progress' label")
            return
            
        print(f"Found {len(tasks)} harvest tasks to process")
        
        for task in tasks:
            issue_number = task['number']
            print(f"Processing task #{issue_number}: {task['title']}")
            
            # Get the task payload from issue body
            body = task.get('body', '')
            try:
                # Extract JSON from the issue body
                task_json = json.loads(body)
                print(f"Task type: {task_json.get('type', 'unknown')}")
                
                # Post a starting comment
                self.github.create_comment(
                    AGENT_TASKS_REPO, 
                    issue_number, 
                    "🔍 Starting task processing..."
                )
                
                # Process the task based on its payload
                result = self.process_task(task_json)
                
                # Store the result in agent-results repository
                self.store_task_result(task_json, result)
                
                # Post completion comment
                self.github.create_comment(
                    AGENT_TASKS_REPO,
                    issue_number,
                    "✅ Task completed successfully! Results stored in agent-results repository."
                )
                
                # Close the issue
                self.github.close_issue(AGENT_TASKS_REPO, issue_number)
                print(f"Task #{issue_number} completed and closed")
                
            except json.JSONDecodeError:
                print(f"Error: Could not parse JSON from issue body for task #{issue_number}")
                self.github.create_comment(
                    AGENT_TASKS_REPO,
                    issue_number,
                    "❌ Error: Could not parse task JSON from issue body. Please check the format."
                )
            except Exception as e:
                print(f"Error processing task #{issue_number}: {str(e)}")
                self.github.create_comment(
                    AGENT_TASKS_REPO,
                    issue_number,
                    f"❌ Error processing task: {str(e)}"
                )
    
    def process_task(self, task_json):
        """Process a specific task based on its type and payload"""
        task_type = task_json.get('type', '')
        task_id = task_json.get('id', 'unknown')
        payload = task_json.get('payload', {})
        
        if task_type == 'harvest':
            print(f"Processing harvest task {task_id} with payload: {payload}")
            return self.process_harvest_task(payload)
        else:
            print(f"Unknown task type: {task_type}")
            return {"error": f"Unknown task type: {task_type}"}
    
    def process_harvest_task(self, payload):
        """Process a harvest task with specific parameters"""
        topics = payload.get('topics', ['ai', 'automation'])
        min_stars = payload.get('min_stars', 10)
        created_after = payload.get('created_after', '2024-01-01')
        count_per_topic = payload.get('count_per_topic', 3)
        
        print(f"Harvesting projects for topics: {topics}, min stars: {min_stars}, created after: {created_after}")
        
        harvested = []
        for topic in topics:
            print(f"Searching for topic: {topic}")
            repos = self.search_repos_by_topic(topic, min_stars, created_after, count_per_topic)
            for repo in repos:
                project_data = self.analyze_project(repo)
                harvested.append(project_data)
                print(f"📦 Harvested: {repo['name']} (Value: {project_data['value_score']})")
        
        return {
            "harvested_projects": harvested,
            "count": len(harvested),
            "topics": topics,
            "timestamp": datetime.now().isoformat()
        }
    
    def store_task_result(self, task_json, result):
        """Store task result in the agent-results repository"""
        task_id = task_json.get('id', 'unknown')
        task_type = task_json.get('type', 'unknown')
        
        # Create the outputs directory structure with today's date
        today = datetime.now().strftime('%Y-%m-%d')
        output_path = f"{OUTPUTS_DIR}/{today}/{task_type}_{task_id}.json"
        
        # Prepare the result JSON with task metadata
        full_result = {
            "task_id": task_id,
            "task_type": task_type,
            "processed_at": datetime.now().isoformat(),
            "result": result
        }
        
        # Convert to JSON string
        result_json = json.dumps(full_result, indent=2)
        
        # Store in the agent-results repository
        response = self.github.create_or_update_file(
            AGENT_RESULTS_REPO,
            output_path,
            result_json,
            f"Add result for task {task_id} ({task_type})"
        )
        
        if response:
            print(f"Result stored successfully at {output_path}")
            return True
        else:
            print(f"Failed to store result at {output_path}")
            return False
    
    def harvest_trending_projects(self):
        """Harvest trending GitHub projects across popular topics"""
        print("⚡ Harvesting trending GitHub projects...")
        topics = ['ai-agent', 'automation', 'saas-template', 'trading-bot']
        
        self.harvested_projects = []  # Reset the harvested projects list
        
        for topic in topics:
            print(f"Searching for topic: {topic}")
            repos = self.search_repos_by_topic(topic)
            for repo in repos[:3]:
                project_data = self.analyze_project(repo)
                self.harvested_projects.append(project_data)
                print(f"📦 Harvested: {repo['name']} (Value: {project_data['value_score']})")
    
    def search_repos_by_topic(self, topic, min_stars=10, created_after='2024-01-01', count=3):
        """Search for repositories by topic with minimum stars"""
        url = "https://api.github.com/search/repositories"
        params = {
            'q': f'topic:{topic} stars:>{min_stars} created:>{created_after}',
            'sort': 'stars',
            'order': 'desc',
            'per_page': count
        }
        
        response = self.github._request('GET', url, params=params)
        if response and 'items' in response:
            return response['items']
        return []
    
    def analyze_project(self, repo):
        """Analyze a GitHub repository and calculate its value score"""
        value_score = self.calculate_value_score(repo)
        
        return {
            'name': repo['name'],
            'full_name': repo['full_name'],
            'url': repo['html_url'],
            'stars': repo['stargazers_count'],
            'description': repo['description'],
            'language': repo['language'],
            'topics': repo.get('topics', []),
            'analyzed_at': datetime.now().isoformat(),
            'value_score': value_score
        }
    
    def calculate_value_score(self, repo):
        """Calculate the value score for a repository based on various metrics"""
        score = 0
        
        # Stars contribute up to 50 points
        score += min(repo['stargazers_count'] / 10, 50)
        
        # High-value topics add 20 points each
        high_value_topics = ['ai', 'automation', 'saas', 'api', 'bot', 'trading']
        repo_topics = repo.get('topics', [])
        description = repo.get('description', '').lower() if repo.get('description') else ''
        
        for topic in high_value_topics:
            if any(topic in t.lower() for t in repo_topics) or topic in description:
                score += 20
        
        # Cap at 100
        return min(round(score, 1), 100)

if __name__ == "__main__":
    github_token = os.getenv('GITHUB_TOKEN') or os.getenv('GH_PAT')
    if not github_token:
        print("Error: No GitHub token found. Set GITHUB_TOKEN or GH_PAT environment variable.")
        exit(1)
        
    harvester = GitHubHarvester(github_token)
    harvester.run()
