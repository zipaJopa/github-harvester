#!/usr/bin/env python3
"""
GitHub Project Harvester - Auto-discover valuable projects
Can run in two modes:
1. Scheduled mode: Runs on a schedule to discover trending projects
2. Task mode: Processes a specific harvest task from an agent-tasks issue
"""
import os
import sys
import json
import time
import base64
import argparse
import requests
from datetime import datetime, timezone

# Configuration
GITHUB_API_URL = "https://api.github.com"
OWNER = "zipaJopa"
AGENT_TASKS_REPO = f"{OWNER}/agent-tasks"
AGENT_RESULTS_REPO = f"{OWNER}/agent-results"
HARVESTED_DIR = "harvested"

class GitHubInteraction:
    """GitHub API interaction helper class"""
    def __init__(self, token):
        self.token = token
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _request(self, method, endpoint, params=None, data=None, max_retries=3, base_url=GITHUB_API_URL):
        """Make a request to the GitHub API with retry logic and rate limit handling"""
        url = f"{base_url}{endpoint}"
        for attempt in range(max_retries):
            try:
                response = self.session.request(method, url, params=params, json=data)
                
                # Handle rate limiting
                if 'X-RateLimit-Remaining' in response.headers and int(response.headers['X-RateLimit-Remaining']) < 10:
                    reset_time = int(response.headers.get('X-RateLimit-Reset', time.time() + 60))
                    sleep_duration = max(0, reset_time - time.time()) + 5
                    print(f"Rate limit low. Sleeping for {sleep_duration:.2f} seconds.")
                    time.sleep(sleep_duration)

                response.raise_for_status()
                return response.json() if response.content else {}
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 403 and "rate limit exceeded" in e.response.text.lower():
                    reset_time = int(e.response.headers.get('X-RateLimit-Reset', time.time() + 60 * (attempt + 1)))
                    sleep_duration = max(0, reset_time - time.time()) + 5
                    print(f"Rate limit exceeded. Retrying in {sleep_duration:.2f}s (attempt {attempt+1}/{max_retries}).")
                    time.sleep(sleep_duration)
                    continue
                elif e.response.status_code == 404 and method == "GET":
                    return None
                print(f"GitHub API request failed ({method} {url}): {e.response.status_code} - {e.response.text}")
                if attempt == max_retries - 1:
                    raise
            except requests.exceptions.RequestException as e:
                print(f"GitHub API request failed ({method} {url}): {e}")
                if attempt == max_retries - 1:
                    raise
            time.sleep(2 ** attempt)  # Exponential backoff
        return {}

    def get_issue(self, repo_full_name, issue_number):
        """Get an issue from a repository"""
        print(f"Fetching issue #{issue_number} from {repo_full_name}...")
        return self._request("GET", f"/repos/{repo_full_name}/issues/{issue_number}")

    def post_comment(self, repo_full_name, issue_number, body):
        """Post a comment on an issue"""
        print(f"Posting comment to issue #{issue_number} in {repo_full_name}...")
        return self._request("POST", f"/repos/{repo_full_name}/issues/{issue_number}/comments", data={"body": body})

    def commit_file(self, repo_full_name, file_path, content_str, commit_message, branch="main"):
        """Commit a file to a repository"""
        print(f"Committing file '{file_path}' to {repo_full_name}...")
        encoded_content = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
        
        # Check if file exists to get its SHA for update
        get_file_endpoint = f"/repos/{repo_full_name}/contents/{file_path}?ref={branch}"
        existing_file_data = self._request("GET", get_file_endpoint)
        
        payload = {
            "message": commit_message,
            "content": encoded_content,
            "branch": branch
        }
        
        if existing_file_data and "sha" in existing_file_data:
            payload["sha"] = existing_file_data["sha"]
        
        put_endpoint = f"/repos/{repo_full_name}/contents/{file_path}"
        response = self._request("PUT", put_endpoint, data=payload)
        if response and "content" in response and "html_url" in response["content"]:
            return response["content"]["html_url"]
        return None


class GitHubHarvester:
    """Harvests valuable GitHub projects based on specified criteria"""
    def __init__(self, github_interaction):
        self.gh = github_interaction
        
    def harvest_trending_projects(self, topics=None, min_stars=10, created_after="2024-01-01", count_per_topic=3):
        """Harvest trending GitHub projects based on specified criteria"""
        print("⚡ Harvesting trending GitHub projects...")
        if topics is None:
            topics = ['ai-agent', 'automation', 'saas-template', 'trading-bot']
        
        all_harvested_projects = []
        for topic in topics:
            print(f"Searching for topic: {topic}")
            repos = self.search_repos_by_topic(topic, min_stars, created_after, count_per_topic)
            for repo_data in repos:
                analyzed_project = self.analyze_project(repo_data)
                all_harvested_projects.append(analyzed_project)
                print(f"📦 Harvested: {analyzed_project['name']} (Value: {analyzed_project['value_score']})")
            time.sleep(1)  # Be respectful to API limits between topics
        return all_harvested_projects
    
    def search_repos_by_topic(self, topic, min_stars, created_after, count_per_topic):
        """Search for repositories by topic"""
        query = f'topic:{topic} stars:>{min_stars} created:>{created_after}'
        params = {
            'q': query,
            'sort': 'stars',
            'order': 'desc',
            'per_page': count_per_topic
        }
        
        response_data = self.gh._request("GET", "/search/repositories", params=params)
        return response_data.get('items', []) if response_data else []
    
    def analyze_project(self, repo):
        """Analyze a GitHub project and calculate its value score"""
        value_score = self.calculate_value_score(repo)
        
        return {
            'id': repo['id'],
            'name': repo['name'],
            'full_name': repo['full_name'],
            'url': repo['html_url'],
            'description': repo['description'],
            'stars': repo['stargazers_count'],
            'forks': repo['forks_count'],
            'language': repo['language'],
            'topics': repo.get('topics', []),
            'created_at': repo['created_at'],
            'updated_at': repo['updated_at'],
            'pushed_at': repo['pushed_at'],
            'license': repo.get('license', {}).get('name') if repo.get('license') else None,
            'harvested_at': datetime.now(timezone.utc).isoformat(),
            'value_score': value_score
        }
    
    def calculate_value_score(self, repo):
        """Calculate a value score for a repository based on various factors"""
        score = 0
        score += min(repo['stargazers_count'] / 10, 50)
        
        high_value_topics = ['ai', 'automation', 'saas', 'api', 'bot']
        topics = repo.get('topics', []) + [repo.get('description', '')]
        for topic in high_value_topics:
            if any(topic in str(t).lower() for t in topics):
                score += 20
        
        return min(score, 100)


def process_harvest_task(issue_number, gh_interaction):
    """Process a harvest task from an agent-tasks issue"""
    print(f"Processing harvest task from issue #{issue_number}...")
    
    # 1. Fetch the task issue
    issue_data = gh_interaction.get_issue(AGENT_TASKS_REPO, issue_number)
    if not issue_data:
        print(f"Error: Could not fetch issue #{issue_number}.")
        return False
    
    # 2. Parse task JSON from issue body
    try:
        task_details_json = issue_data.get("body", "{}")
        if not task_details_json.strip():
            task_details_json = "{}"
        task_details = json.loads(task_details_json)
        
        task_id = task_details.get("id", f"unknown_id_{issue_number}")
        task_type = task_details.get("type", "unknown_type")
        task_payload = task_details.get("payload", {})
        
        if task_type != "harvest":
            print(f"Error: Expected task type 'harvest', but got '{task_type}'.")
            gh_interaction.post_comment(AGENT_TASKS_REPO, issue_number, 
                f"❌ Task failed: Expected task type 'harvest', but got '{task_type}'.")
            return False
        
        print(f"Successfully parsed task: ID={task_id}, Type={task_type}")
    except json.JSONDecodeError as e:
        err_msg = f"Error parsing task JSON from issue #{issue_number} body: {e}. Body: '{issue_data.get('body', '')[:200]}...'"
        print(err_msg)
        gh_interaction.post_comment(AGENT_TASKS_REPO, issue_number, 
            f"❌ Task failed: Could not parse task JSON.\nError: {e}")
        return False
    
    # 3. Post "Task started" comment
    start_comment = f"🚀 Task `{task_id}` (Type: `{task_type}`) started execution by `github-harvester-bot`."
    gh_interaction.post_comment(AGENT_TASKS_REPO, issue_number, start_comment)
    
    # 4. Execute the harvest task
    try:
        # Extract parameters from payload or use defaults
        topics = task_payload.get('topics', ['ai', 'agent', 'automation', 'llm'])
        min_stars = task_payload.get('min_stars', 50)
        created_after = task_payload.get('created_after', '2024-01-01')
        count_per_topic = task_payload.get('count_per_topic', 5)
        
        # Create harvester and run it
        harvester = GitHubHarvester(gh_interaction)
        harvested_projects = harvester.harvest_trending_projects(
            topics=topics,
            min_stars=min_stars,
            created_after=created_after,
            count_per_topic=count_per_topic
        )
        
        # 5. Store results in agent-results repository
        result_data = {
            "task_id": task_id,
            "task_type": task_type,
            "execution_time": datetime.now(timezone.utc).isoformat(),
            "parameters": {
                "topics": topics,
                "min_stars": min_stars,
                "created_after": created_after,
                "count_per_topic": count_per_topic
            },
            "harvested_count": len(harvested_projects),
            "harvested_projects": harvested_projects
        }
        
        # Format the date for the file path
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        file_path = f"outputs/{date_str}/harvest_{task_id.replace(':', '_')}.json"
        commit_message = f"feat: Store results for harvest task {task_id}"
        
        result_url = gh_interaction.commit_file(
            AGENT_RESULTS_REPO, 
            file_path, 
            json.dumps(result_data, indent=2), 
            commit_message
        )
        
        if not result_url:
            print(f"Error: Failed to store results for task {task_id}.")
            gh_interaction.post_comment(AGENT_TASKS_REPO, issue_number, 
                f"❌ Task failed: Could not store results in {AGENT_RESULTS_REPO}.")
            return False
        
        # 6. Post completion comment
        done_comment = f"DONE ✅ Task `{task_id}` (Type: `{task_type}`) completed successfully.\n\n"
        done_comment += f"📊 **Results Summary:**\n"
        done_comment += f"- Topics searched: {', '.join(topics)}\n"
        done_comment += f"- Projects harvested: {len(harvested_projects)}\n"
        done_comment += f"- Top value scores: {', '.join([f'{p['name']} ({p['value_score']})' for p in sorted(harvested_projects, key=lambda x: x['value_score'], reverse=True)[:3]])}\n\n"
        done_comment += f"📄 Full results stored at: {result_url}"
        
        gh_interaction.post_comment(AGENT_TASKS_REPO, issue_number, done_comment)
        print(f"✅ Task {task_id} completed successfully. Results stored at: {result_url}")
        return True
        
    except Exception as e:
        import traceback
        error_message = f"An unexpected error occurred during task execution: {str(e)}"
        print(f"Error: {error_message}")
        traceback.print_exc()
        
        # Post error comment
        gh_interaction.post_comment(AGENT_TASKS_REPO, issue_number, 
            f"❌ Task failed: {error_message}\n\n```\n{traceback.format_exc()}\n```")
        return False


def run_scheduled_harvest(gh_interaction):
    """Run the regular scheduled harvest"""
    print("Running scheduled harvest...")
    harvester = GitHubHarvester(gh_interaction)
    harvested_projects = harvester.harvest_trending_projects()
    
    # Store results locally in the harvested directory
    os.makedirs(HARVESTED_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    result_file = f"{HARVESTED_DIR}/harvest_{timestamp}.json"
    
    with open(result_file, 'w') as f:
        json.dump(harvested_projects, f, indent=2)
    
    print(f"Harvest completed. Found {len(harvested_projects)} projects. Results saved to {result_file}")
    return True


def main():
    """Main entry point with argument parsing"""
    parser = argparse.ArgumentParser(description='GitHub Project Harvester')
    parser.add_argument('--task-mode', action='store_true', help='Run in task mode (process a specific task)')
    parser.add_argument('--issue-number', type=int, help='Issue number to process in task mode')
    args = parser.parse_args()
    
    github_token = os.getenv('GITHUB_TOKEN')
    if not github_token:
        print("Error: GITHUB_TOKEN environment variable not set.")
        sys.exit(1)
    
    gh_interaction = GitHubInteraction(github_token)
    
    if args.task_mode:
        if not args.issue_number:
            print("Error: --issue-number is required in task mode.")
            sys.exit(1)
        
        success = process_harvest_task(args.issue_number, gh_interaction)
        sys.exit(0 if success else 1)
    else:
        # Run in regular scheduled mode
        success = run_scheduled_harvest(gh_interaction)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
