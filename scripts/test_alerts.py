#!/usr/bin/env python3
"""
Alert System Smoke Test
Tests alert webhook configuration and delivery for production readiness.

Usage:
    python scripts/test_alerts.py [--webhook-url URL] [--dry-run]

Environment Variables:
    ALERT_WEBHOOK_URL: Primary alert webhook (Slack/PagerDuty/etc)
    SLACK_WEBHOOK_URL: Alternative Slack webhook
"""

import argparse
import json
import os
import sys
import time
from typing import Dict, Any

from infra.alerting import AlertService, AlertSeverity, AlertConfig


def test_alert_delivery(
    webhook_url: str,
    dry_run: bool = False,
    test_name: str = "Production Alert System Test"
) -> bool:
    """Test alert delivery to configured webhook."""
    
    print(f"\n{'='*70}")
    print(f"  {test_name}")
    print(f"{'='*70}\n")
    
    if dry_run:
        print("⚠️  DRY RUN MODE - No actual webhooks will be called\n")
    
    # Create alert service
    config = AlertConfig(
        enabled=True,
        webhook_url=webhook_url,
        min_severity=AlertSeverity.INFO,
        dry_run=dry_run,
        timeout=10.0
    )
    
    alert_service = AlertService(config)
    
    if not alert_service.is_enabled():
        print("❌ FAILED: AlertService is not enabled")
        print(f"   Webhook URL: {webhook_url}")
        return False
    
    print(f"✅ AlertService initialized")
    print(f"   Webhook URL: {webhook_url[:50]}..." if len(webhook_url) > 50 else f"   Webhook URL: {webhook_url}")
    print(f"   Dry Run: {dry_run}")
    print(f"   Timeout: {config.timeout}s\n")
    
    # Test suite: different severity levels
    test_cases = [
        {
            "severity": AlertSeverity.INFO,
            "title": "🧪 Test Alert - INFO",
            "message": "This is a test INFO alert from 247trader-v2",
            "context": {"test_type": "info", "timestamp": time.time()}
        },
        {
            "severity": AlertSeverity.WARNING,
            "title": "⚠️ Test Alert - WARNING",
            "message": "This is a test WARNING alert from 247trader-v2",
            "context": {
                "test_type": "warning",
                "example_issue": "Daily stop loss approaching (-2.5%)",
                "timestamp": time.time()
            }
        },
        {
            "severity": AlertSeverity.CRITICAL,
            "title": "🚨 Test Alert - CRITICAL",
            "message": "This is a test CRITICAL alert from 247trader-v2",
            "context": {
                "test_type": "critical",
                "example_event": "Kill switch activated",
                "system_state": "HALTED",
                "timestamp": time.time()
            }
        }
    ]
    
    print(f"Running {len(test_cases)} alert delivery tests...\n")
    
    success_count = 0
    for i, test_case in enumerate(test_cases, 1):
        print(f"Test {i}/{len(test_cases)}: {test_case['severity'].name}")
        
        try:
            # Send alert
            alert_service.notify(
                severity=test_case["severity"],
                title=test_case["title"],
                message=test_case["message"],
                context=test_case["context"]
            )
            
            print(f"   ✅ Alert sent successfully")
            print(f"      Title: {test_case['title']}")
            print(f"      Message: {test_case['message']}")
            
            success_count += 1
            
            # Brief delay between alerts
            if i < len(test_cases):
                time.sleep(1)
                
        except Exception as exc:
            print(f"   ❌ Alert delivery FAILED: {exc}")
            return False
        
        print()
    
    # Summary
    print(f"{'='*70}")
    print(f"  Test Results: {success_count}/{len(test_cases)} alerts delivered")
    print(f"{'='*70}\n")
    
    if success_count == len(test_cases):
        print("✅ ALL TESTS PASSED")
        if not dry_run:
            print("\n📬 Check your alert destination to verify delivery!")
            print("   (Slack channel, PagerDuty, email, etc.)\n")
        return True
    else:
        print("❌ SOME TESTS FAILED")
        return False


def test_production_alert_scenarios(webhook_url: str, dry_run: bool = False) -> bool:
    """Test realistic production alert scenarios."""
    
    print(f"\n{'='*70}")
    print(f"  Production Alert Scenario Tests")
    print(f"{'='*70}\n")
    
    config = AlertConfig(
        enabled=True,
        webhook_url=webhook_url,
        min_severity=AlertSeverity.WARNING,  # Production threshold
        dry_run=dry_run,
        timeout=10.0
    )
    
    alert_service = AlertService(config)
    
    # Realistic production scenarios
    scenarios = [
        {
            "name": "Kill Switch Activated",
            "severity": AlertSeverity.CRITICAL,
            "title": "🚨 KILL SWITCH ACTIVATED",
            "message": "Trading halted: data/KILL_SWITCH file detected",
            "context": {
                "action": "all_trading_halted",
                "open_positions": 3,
                "open_orders": 1,
                "unrealized_pnl_pct": -2.3,
                "timestamp": time.time()
            }
        },
        {
            "name": "Daily Stop Loss Hit",
            "severity": AlertSeverity.CRITICAL,
            "title": "🛑 Daily Stop Loss Triggered",
            "message": "Daily PnL breached -3.0% threshold, new trades blocked",
            "context": {
                "daily_pnl_pct": -3.2,
                "threshold": -3.0,
                "nav_start": 1000.0,
                "nav_current": 968.0,
                "timestamp": time.time()
            }
        },
        {
            "name": "Exchange Circuit Breaker",
            "severity": AlertSeverity.CRITICAL,
            "title": "⚡ Exchange Circuit Breaker Tripped",
            "message": "Exchange health check failed: 3 consecutive API errors",
            "context": {
                "error_count": 3,
                "last_error": "HTTPError 503 Service Unavailable",
                "window_seconds": 300,
                "action": "trading_paused",
                "timestamp": time.time()
            }
        },
        {
            "name": "Reconciliation Mismatch",
            "severity": AlertSeverity.WARNING,
            "title": "⚠️ Position Reconciliation Mismatch",
            "message": "Local state diverged from exchange snapshot",
            "context": {
                "symbol": "BTC-USD",
                "local_qty": 0.01,
                "exchange_qty": 0.009,
                "action": "syncing_to_exchange",
                "timestamp": time.time()
            }
        },
        {
            "name": "Order Rejection Burst",
            "severity": AlertSeverity.WARNING,
            "title": "⚠️ Multiple Order Rejections",
            "message": "5 orders rejected in last 10 minutes",
            "context": {
                "rejection_count": 5,
                "window_minutes": 10,
                "reasons": ["INSUFFICIENT_FUNDS", "MIN_SIZE_NOT_MET", "PRODUCT_NOT_AVAILABLE"],
                "timestamp": time.time()
            }
        }
    ]
    
    print(f"Testing {len(scenarios)} production alert scenarios...\n")
    
    success_count = 0
    for scenario in scenarios:
        print(f"Scenario: {scenario['name']}")
        
        try:
            alert_service.notify(
                severity=scenario["severity"],
                title=scenario["title"],
                message=scenario["message"],
                context=scenario["context"]
            )
            
            print(f"   ✅ Alert delivered")
            success_count += 1
            time.sleep(1)
            
        except Exception as exc:
            print(f"   ❌ FAILED: {exc}")
            return False
        
        print()
    
    print(f"{'='*70}")
    print(f"  Scenario Results: {success_count}/{len(scenarios)} alerts delivered")
    print(f"{'='*70}\n")
    
    return success_count == len(scenarios)


def main():
    parser = argparse.ArgumentParser(
        description="Test alert webhook configuration and delivery",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--webhook-url",
        help="Alert webhook URL (defaults to ALERT_WEBHOOK_URL env var)"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log alerts without actually sending webhooks"
    )
    
    parser.add_argument(
        "--scenarios-only",
        action="store_true",
        help="Only test production scenarios (skip basic tests)"
    )
    
    args = parser.parse_args()
    
    # Get webhook URL
    webhook_url = args.webhook_url or os.getenv("ALERT_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_URL")
    
    if not webhook_url:
        print("❌ ERROR: No webhook URL configured\n")
        print("Please provide webhook URL via:")
        print("  1. --webhook-url argument")
        print("  2. ALERT_WEBHOOK_URL environment variable")
        print("  3. SLACK_WEBHOOK_URL environment variable\n")
        print("Example:")
        print("  export ALERT_WEBHOOK_URL='https://hooks.slack.com/services/YOUR/WEBHOOK/URL'")
        print("  python scripts/test_alerts.py\n")
        return 1
    
    # Run tests
    try:
        if not args.scenarios_only:
            # Basic delivery tests
            success = test_alert_delivery(webhook_url, args.dry_run)
            if not success:
                return 1
            
            if not args.dry_run:
                print("\n⏸️  Pausing 3 seconds before scenario tests...\n")
                time.sleep(3)
        
        # Production scenario tests
        success = test_production_alert_scenarios(webhook_url, args.dry_run)
        
        if success:
            print("\n" + "="*70)
            print("  ✅ ALL ALERT TESTS PASSED")
            print("="*70)
            print("\n🎉 Alert system is production-ready!\n")
            
            if not args.dry_run:
                print("Next steps:")
                print("  1. Verify all alerts appeared in your destination")
                print("  2. Check alert formatting and readability")
                print("  3. Confirm on-call routing is working")
                print("  4. Test alert response procedures\n")
            
            return 0
        else:
            print("\n" + "="*70)
            print("  ❌ SOME ALERT TESTS FAILED")
            print("="*70)
            print("\nPlease review errors and fix configuration.\n")
            return 1
            
    except KeyboardInterrupt:
        print("\n\n⚠️  Tests interrupted by user\n")
        return 130
    except Exception as exc:
        print(f"\n❌ UNEXPECTED ERROR: {exc}\n")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
