#!/usr/bin/env python3
"""
Quick verification that Prometheus metrics are being exposed correctly.

Usage:
    python scripts/verify_prometheus.py
"""

import requests
import sys


def verify_metrics_endpoint(url: str = "http://localhost:8000/metrics") -> bool:
    """Check if Prometheus metrics endpoint is accessible and returning data"""
    print(f"🔍 Checking Prometheus metrics endpoint: {url}")
    
    try:
        response = requests.get(url, timeout=5)
        
        if response.status_code != 200:
            print(f"❌ HTTP {response.status_code}: Endpoint not accessible")
            return False
        
        content = response.text
        
        # Check for expected metrics
        expected_metrics = [
            "trader_account_value_usd",
            "trader_daily_pnl_pct",
            "trader_exposure_pct",
            "trader_open_positions",
            "trader_cycle_duration_seconds",
        ]
        
        missing = []
        found = []
        
        for metric in expected_metrics:
            if metric in content:
                found.append(metric)
            else:
                missing.append(metric)
        
        print(f"\n✅ Metrics endpoint is accessible")
        print(f"📊 Found {len(found)}/{len(expected_metrics)} expected metrics:")
        for metric in found:
            print(f"   ✓ {metric}")
        
        if missing:
            print(f"\n⚠️  Missing metrics (may not be initialized yet):")
            for metric in missing:
                print(f"   ✗ {metric}")
        
        # Show sample of content
        lines = content.split('\n')
        metric_lines = [l for l in lines if l and not l.startswith('#')][:5]
        
        if metric_lines:
            print(f"\n📝 Sample metrics:")
            for line in metric_lines:
                print(f"   {line}")
        
        return True
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Connection failed: Is the trading bot running with prometheus_enabled: true?")
        print(f"   Start the bot with: ./app_run_live.sh")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def verify_prometheus_scraping(url: str = "http://localhost:9090/api/v1/targets") -> bool:
    """Check if Prometheus is scraping the bot successfully"""
    print(f"\n🔍 Checking Prometheus scraping status: {url}")
    
    try:
        response = requests.get(url, timeout=5)
        
        if response.status_code != 200:
            print(f"❌ Prometheus not accessible (is Docker Compose running?)")
            print(f"   Start with: ./scripts/start_monitoring.sh")
            return False
        
        data = response.json()
        targets = data.get("data", {}).get("activeTargets", [])
        
        trader_targets = [t for t in targets if "247trader" in t.get("labels", {}).get("job", "")]
        
        if not trader_targets:
            print(f"⚠️  No 247trader targets found in Prometheus")
            print(f"   Check config/prometheus.yml and restart stack")
            return False
        
        for target in trader_targets:
            health = target.get("health", "unknown")
            last_scrape = target.get("lastScrape", "never")
            scrape_url = target.get("scrapeUrl", "unknown")
            
            if health == "up":
                print(f"✅ Target is UP: {scrape_url}")
                print(f"   Last scrape: {last_scrape}")
            else:
                print(f"❌ Target is {health.upper()}: {scrape_url}")
                print(f"   Last error: {target.get('lastError', 'none')}")
        
        return True
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Prometheus not running")
        print(f"   Start with: ./scripts/start_monitoring.sh")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    """Run all verification checks"""
    print("=" * 60)
    print("  Prometheus Metrics Verification")
    print("=" * 60)
    
    # Check bot metrics endpoint
    bot_ok = verify_metrics_endpoint()
    
    # Check Prometheus scraping
    prom_ok = verify_prometheus_scraping()
    
    print("\n" + "=" * 60)
    if bot_ok and prom_ok:
        print("✅ All checks passed!")
        print("\n📈 View dashboards:")
        print("   Grafana:    http://localhost:3000 (admin/admin)")
        print("   Prometheus: http://localhost:9090")
        print("   Bot Metrics: http://localhost:8000/metrics")
        return 0
    elif bot_ok:
        print("⚠️  Bot metrics OK, but Prometheus not scraping")
        print("   Start monitoring stack: ./scripts/start_monitoring.sh")
        return 1
    else:
        print("❌ Bot metrics not accessible")
        print("\n💡 Troubleshooting:")
        print("   1. Enable in config/app.yaml: prometheus_enabled: true")
        print("   2. Start bot: ./app_run_live.sh")
        print("   3. Start monitoring: ./scripts/start_monitoring.sh")
        return 2


if __name__ == "__main__":
    sys.exit(main())
