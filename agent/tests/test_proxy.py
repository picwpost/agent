from __future__ import annotations

import json
import os
import shutil
import unittest
from hashlib import sha512 as sha
from unittest.mock import patch

from agent.proxy import Proxy


class TestProxy(unittest.TestCase):
    """Tests for class methods of Proxy."""

    def _create_needed_files(self):
        """Create host dirs for 2 domains test json files."""
        os.makedirs(os.path.join(self.hosts_directory, self.domain_1), exist_ok=True)
        os.makedirs(os.path.join(self.hosts_directory, self.domain_2), exist_ok=True)
        os.makedirs(self.upstreams_directory, exist_ok=True)

        map_1 = os.path.join(self.hosts_directory, self.domain_1, "map.json")
        map_2 = os.path.join(self.hosts_directory, self.domain_2, "map.json")

        with open(map_1, "w") as m:
            json.dump({self.domain_1: self.default_domain}, m)
        with open(map_2, "w") as m:
            json.dump({self.domain_2: self.default_domain}, m)

    def setUp(self):
        self.test_dir = "test_dir"
        if os.path.exists(self.test_dir):
            raise FileExistsError(
                f"""
                Directory {self.test_dir} exists.  This directory will be used
                for running tests and will be deleted
                """
            )

        self.default_domain = "xxx.frappe.cloud"
        self.domain_1 = "balu.codes"
        self.domain_2 = "www.balu.codes"
        self.tld = "frappe.cloud"

        self.hosts_directory = os.path.join(self.test_dir, "nginx/hosts")
        self.upstreams_directory = os.path.join(self.test_dir, "nginx/upstreams")
        self._create_needed_files()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _get_fake_proxy(self):
        """Get Proxy object with only config and hosts_directory attrs."""
        with patch.object(Proxy, "__init__", new=lambda x: None):
            proxy = Proxy()
        proxy.hosts_directory = self.hosts_directory
        proxy.nginx_directory = os.path.join(self.test_dir, "nginx")
        proxy._proxy_config_modification_lock = None
        return proxy

    def test_hosts_redirects_default_domain(self):
        """
        Ensure hosts property redirects default domain when redirect.json is
        present.
        """
        proxy = self._get_fake_proxy()
        os.makedirs(os.path.join(self.hosts_directory, self.default_domain))
        redirect_file = os.path.join(self.hosts_directory, self.default_domain, "redirect.json")
        with open(redirect_file, "w") as r:
            json.dump({self.default_domain: self.domain_1}, r)

        self.assertLessEqual(
            {
                self.default_domain: {
                    "redirect": self.domain_1,
                    "codeserver": False,
                }
            }.items(),
            proxy.hosts.items(),
        )

    def _test_add_host(self, proxy, host):
        # TODO: test contents of map.json and certificate dirs
        with patch.object(Proxy, "add_host", new=Proxy.add_host.__wrapped__):
            # get undecorated method with __wrapped__
            proxy.add_host(host, "www.test.com", {})

        self.assertTrue(os.path.exists(os.path.join(proxy.hosts_directory, host, "map.json")))

    def test_add_hosts_works_without_hosts_dir(self):
        """Ensure add_host works when hosts directory doesn't exist."""
        proxy = self._get_fake_proxy()
        shutil.rmtree(proxy.hosts_directory)
        self._test_add_host(proxy, "test.com")

    def test_add_hosts_works_with_hosts_dir(self):
        """Ensure add_host works when hosts directory exists."""
        proxy = self._get_fake_proxy()
        self._test_add_host(proxy, "test.com")

    def test_add_hosts_works_with_host_dir(self):
        """Ensure add_host works when host directory of host exists."""
        proxy = self._get_fake_proxy()
        host = "test.com"
        host_directory = os.path.join(proxy.hosts_directory, host)
        os.mkdir(host_directory)
        self._test_add_host(proxy, host)

    def _test_add_upstream(self, proxy, upstream):
        upstream_dir = os.path.join(proxy.upstreams_directory, upstream)
        with patch.object(Proxy, "add_upstream", new=Proxy.add_upstream.__wrapped__):
            # get undecorated method with __wrapped__
            proxy.add_upstream(upstream)
        self.assertTrue(os.path.exists(upstream_dir))

    def test_add_upstream_works_with_upstreams_dir(self):
        """Ensure add_upstream works when upstreams directory exists."""
        proxy = self._get_fake_proxy()
        proxy.upstreams_directory = self.upstreams_directory
        self._test_add_upstream(proxy, "0.0.0.0")

    def test_add_upstream_works_without_upstreams_dir(self):
        """Ensure add_upstream works when upstreams directory doesn't exist."""
        proxy = self._get_fake_proxy()
        proxy.upstreams_directory = self.upstreams_directory
        os.rmdir(proxy.upstreams_directory)
        self._test_add_upstream(proxy, "0.0.0.0")

    def test_remove_redirect_for_default_domain_deletes_host_dir(self):
        """Ensure removing redirect of default domain deletes the host dir."""
        proxy = self._get_fake_proxy()
        proxy.domain = self.tld
        host_dir = os.path.join(self.hosts_directory, self.default_domain)
        os.makedirs(host_dir)
        redir_file = os.path.join(host_dir, "redirect.json")
        with open(redir_file, "w") as r:
            json.dump({self.default_domain: self.domain_1}, r)

        with patch.object(Proxy, "remove_redirect", new=Proxy.remove_redirect.__wrapped__):
            proxy.remove_redirect(self.default_domain)
        self.assertFalse(os.path.exists(redir_file))
        self.assertFalse(os.path.exists(host_dir))

    def test_setup_redirect_creates_redirect_json_for_given_hosts(self):
        """Ensure setup redirect creates redirect.json files"""
        proxy = self._get_fake_proxy()
        proxy.domain = self.tld
        host = self.domain_2
        target = self.domain_1
        with patch.object(Proxy, "setup_redirect", new=Proxy.setup_redirect.__wrapped__):
            proxy.setup_redirect(host, target)
        host_dir = os.path.join(proxy.hosts_directory, host)
        redir_file = os.path.join(host_dir, "redirect.json")
        self.assertTrue(os.path.exists(redir_file))

    def test_remove_redirect_deletes_redirect_json_for_given_hosts(self):
        """Ensure remove redirect deletes redirect.json files"""
        proxy = self._get_fake_proxy()
        proxy.domain = self.tld
        host = self.domain_2
        target = self.domain_1
        with patch.object(Proxy, "setup_redirect", new=Proxy.setup_redirect.__wrapped__):
            proxy.setup_redirect(host, target)
            # assume that setup redirects works properly based on previous test
        with patch.object(Proxy, "remove_redirect", new=Proxy.remove_redirect.__wrapped__):
            proxy.remove_redirect(host)
        host_dir = os.path.join(proxy.hosts_directory, host)
        redir_file = os.path.join(host_dir, "redirect.json")
        self.assertFalse(os.path.exists(redir_file))

    def test_rename_on_site_host_renames_host_directory(self):
        """Ensure rename site renames host directory."""
        proxy = self._get_fake_proxy()
        old_host_dir = os.path.join(proxy.hosts_directory, self.default_domain)
        os.makedirs(old_host_dir)
        with patch.object(
            Proxy,
            "rename_host_dir",
            new=Proxy.rename_host_dir.__wrapped__,
        ):
            proxy.rename_host_dir(self.default_domain, "yyy.frappe.cloud")
        new_host_dir = os.path.join(proxy.hosts_directory, "yyy.frappe.cloud")
        self.assertFalse(os.path.exists(old_host_dir))
        self.assertTrue(os.path.exists(new_host_dir))

    def test_rename_on_site_host_renames_redirect_json(self):
        """Ensure rename site updates redirect.json if exists."""
        proxy = self._get_fake_proxy()
        old_host_dir = os.path.join(proxy.hosts_directory, self.default_domain)
        os.makedirs(old_host_dir)
        redirect_file = os.path.join(old_host_dir, "redirect.json")
        with open(redirect_file, "w") as r:
            json.dump({self.default_domain: self.domain_1}, r)
        with patch.object(
            Proxy,
            "rename_host_dir",
            new=Proxy.rename_host_dir.__wrapped__,
        ):
            proxy.rename_host_dir(self.default_domain, "yyy.frappe.cloud")
        with patch.object(
            Proxy,
            "rename_site_in_host_dir",
            new=Proxy.rename_site_in_host_dir.__wrapped__,
        ):
            proxy.rename_site_in_host_dir("yyy.frappe.cloud", self.default_domain, "yyy.frappe.cloud")
        new_host_dir = os.path.join(proxy.hosts_directory, "yyy.frappe.cloud")
        redirect_file = os.path.join(new_host_dir, "redirect.json")
        with open(redirect_file) as r:
            self.assertDictEqual(json.load(r), {"yyy.frappe.cloud": self.domain_1})

    def test_rename_updates_map_json_of_custom(self):
        """Ensure custom domains have map.json updated on site rename."""
        proxy = self._get_fake_proxy()
        with patch.object(
            Proxy,
            "rename_site_in_host_dir",
            new=Proxy.rename_site_in_host_dir.__wrapped__,
        ):
            proxy.rename_site_in_host_dir(self.domain_1, self.default_domain, "yyy.frappe.cloud")
        host_directory = os.path.join(proxy.hosts_directory, self.domain_1)
        map_file = os.path.join(host_directory, "map.json")
        with open(map_file) as m:
            self.assertDictEqual(json.load(m), {self.domain_1: "yyy.frappe.cloud"})

    def test_rename_updates_redirect_json_of_custom(self):
        """Ensure redirect.json updated for domains redirected to default."""
        proxy = self._get_fake_proxy()
        host_directory = os.path.join(proxy.hosts_directory, self.domain_1)
        redirect_file = os.path.join(host_directory, "redirect.json")
        with open(redirect_file, "w") as r:
            json.dump({self.domain_1: self.default_domain}, r)
        with patch.object(
            Proxy,
            "rename_site_in_host_dir",
            new=Proxy.rename_site_in_host_dir.__wrapped__,
        ):
            proxy.rename_site_in_host_dir(self.domain_1, self.default_domain, "yyy.frappe.cloud")
        redirect_file = os.path.join(host_directory, "redirect.json")
        with open(redirect_file) as r:
            self.assertDictEqual(json.load(r), {self.domain_1: "yyy.frappe.cloud"})

    def test_rename_does_not_update_redirect_json_of_custom(self):
        """Test redirects not updated for domains not redirected to default."""
        proxy = self._get_fake_proxy()
        host_directory = os.path.join(proxy.hosts_directory, self.domain_1)
        redirect_file = os.path.join(host_directory, "redirect.json")
        original_dict = {self.domain_1: self.domain_2}
        with open(redirect_file, "w") as r:
            json.dump(original_dict, r)
        with patch.object(
            Proxy,
            "rename_site_in_host_dir",
            new=Proxy.rename_site_in_host_dir.__wrapped__,
        ):
            proxy.rename_site_in_host_dir(self.domain_1, self.default_domain, "yyy.frappe.cloud")
        with open(redirect_file) as r:
            self.assertDictEqual(json.load(r), original_dict)

    def test_rename_does_not_update_partial_strings(self):
        """Test rename doesn't update part of other custom domains."""
        proxy = self._get_fake_proxy()
        custom_domain = self.default_domain + ".balu.codes"
        self.domain_1 = custom_domain
        self._create_needed_files()
        with patch.object(
            Proxy,
            "rename_site_in_host_dir",
            new=Proxy.rename_site_in_host_dir.__wrapped__,
        ):
            proxy.rename_site_in_host_dir(self.domain_1, self.default_domain, "yyy.frappe.cloud")
        host_dir = os.path.join(self.hosts_directory, self.domain_1)
        map_file = os.path.join(host_dir, "map.json")
        with open(map_file) as m:
            self.assertDictEqual(json.load(m), {self.domain_1: "yyy.frappe.cloud"})


class TestProxySiteMapSharding(unittest.TestCase):
    """Tests for the sharded per-site $upstream_server_hash/$socket_upstream_hash map files."""

    def setUp(self):
        self.test_dir = "test_dir_map_sharding"
        if os.path.exists(self.test_dir):
            raise FileExistsError(f"Directory {self.test_dir} exists and would be deleted by this test.")

        self.nginx_directory = os.path.join(self.test_dir, "nginx")
        self.upstreams_directory = os.path.join(self.nginx_directory, "upstreams")
        os.makedirs(self.upstreams_directory)

        with patch.object(Proxy, "__init__", new=lambda x: None):
            self.proxy = Proxy()
        self.proxy.nginx_directory = self.nginx_directory
        self.proxy.upstreams_directory = self.upstreams_directory
        self.proxy.upstream_map_directory = os.path.join(self.nginx_directory, "upstream-map.d")
        self.proxy.socket_map_directory = os.path.join(self.nginx_directory, "socket-map.d")
        self.proxy.secondary_config_path = os.path.join(self.nginx_directory, "secondaries.json")
        self.proxy._proxy_config_modification_lock = None

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _add_site_file(self, upstream, site, status="active"):
        upstream_directory = os.path.join(self.upstreams_directory, upstream)
        os.makedirs(upstream_directory, exist_ok=True)
        with open(os.path.join(upstream_directory, site), "w") as f:
            f.write(status)

    def _read_map_entry(self, directory, site):
        with open(os.path.join(directory, f"{site}.map")) as f:
            return f.read()

    def test_write_site_map_entry_uses_hashed_upstream_for_active_site(self):
        self._add_site_file("10.0.0.1", "mysite.frappe.cloud", status="active")
        self.proxy.write_site_map_entry("10.0.0.1", "mysite.frappe.cloud")

        hashed_upstream = sha(b"10.0.0.1").hexdigest()[:16]
        entry = self._read_map_entry(self.proxy.upstream_map_directory, "mysite.frappe.cloud")
        self.assertEqual(entry, f"mysite.frappe.cloud http://{hashed_upstream};\n")

        socket_entry = self._read_map_entry(self.proxy.socket_map_directory, "mysite.frappe.cloud")
        self.assertEqual(socket_entry, f"mysite.frappe.cloud http://{hashed_upstream};\n")

    def test_write_site_map_entry_uses_status_upstream_for_suspended_site(self):
        self._add_site_file("10.0.0.1", "mysite.frappe.cloud", status="suspended")
        self.proxy.write_site_map_entry("10.0.0.1", "mysite.frappe.cloud")

        entry = self._read_map_entry(self.proxy.upstream_map_directory, "mysite.frappe.cloud")
        self.assertEqual(entry, "mysite.frappe.cloud http://suspended;\n")

    def test_write_site_map_entry_uses_primary_suffix_for_auto_scaled_site(self):
        self._add_site_file("10.0.0.1", "mysite.frappe.cloud", status="active")
        os.makedirs(os.path.dirname(self.proxy.secondary_config_path), exist_ok=True)
        with open(self.proxy.secondary_config_path, "w") as f:
            json.dump({"10.0.0.1": [{"10.0.0.2": 1}]}, f)

        self.proxy.write_site_map_entry("10.0.0.1", "mysite.frappe.cloud")

        hashed_upstream = sha(b"10.0.0.1").hexdigest()[:16]
        socket_entry = self._read_map_entry(self.proxy.socket_map_directory, "mysite.frappe.cloud")
        self.assertEqual(socket_entry, f"mysite.frappe.cloud http://{hashed_upstream}_primary;\n")

        # Non-socket map is unaffected by auto-scaling.
        entry = self._read_map_entry(self.proxy.upstream_map_directory, "mysite.frappe.cloud")
        self.assertEqual(entry, f"mysite.frappe.cloud http://{hashed_upstream};\n")

    def test_write_site_map_entry_is_noop_when_site_file_missing(self):
        self.proxy.write_site_map_entry("10.0.0.1", "ghost.frappe.cloud")
        self.assertFalse(os.path.exists(self.proxy.upstream_map_directory))
        self.assertFalse(os.path.exists(self.proxy.socket_map_directory))

    def test_remove_site_map_entry_deletes_both_files(self):
        self._add_site_file("10.0.0.1", "mysite.frappe.cloud")
        self.proxy.write_site_map_entry("10.0.0.1", "mysite.frappe.cloud")

        self.proxy.remove_site_map_entry("mysite.frappe.cloud")

        self.assertFalse(
            os.path.exists(os.path.join(self.proxy.upstream_map_directory, "mysite.frappe.cloud.map"))
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.proxy.socket_map_directory, "mysite.frappe.cloud.map"))
        )

    def test_remove_site_map_entry_is_noop_when_absent(self):
        # Should not raise even though nothing was ever written.
        self.proxy.remove_site_map_entry("never-existed.frappe.cloud")

    def test_add_site_to_upstream_writes_map_entry(self):
        upstream_directory = os.path.join(self.upstreams_directory, "10.0.0.1")
        os.makedirs(upstream_directory, exist_ok=True)
        with patch.object(Proxy, "add_site_to_upstream", new=Proxy.add_site_to_upstream.__wrapped__):
            self.proxy.add_site_to_upstream("10.0.0.1", "mysite.frappe.cloud")

        hashed_upstream = sha(b"10.0.0.1").hexdigest()[:16]
        entry = self._read_map_entry(self.proxy.upstream_map_directory, "mysite.frappe.cloud")
        self.assertEqual(entry, f"mysite.frappe.cloud http://{hashed_upstream};\n")

    def test_update_site_status_rewrites_map_entry(self):
        self._add_site_file("10.0.0.1", "mysite.frappe.cloud", status="active")
        self.proxy.write_site_map_entry("10.0.0.1", "mysite.frappe.cloud")

        with patch.object(Proxy, "update_site_status", new=Proxy.update_site_status.__wrapped__):
            self.proxy.update_site_status("10.0.0.1", "mysite.frappe.cloud", "suspended")

        entry = self._read_map_entry(self.proxy.upstream_map_directory, "mysite.frappe.cloud")
        self.assertEqual(entry, "mysite.frappe.cloud http://suspended;\n")

    def test_remove_site_from_upstream_removes_map_entry(self):
        upstream_directory = os.path.join(self.upstreams_directory, "10.0.0.1")
        self._add_site_file("10.0.0.1", "mysite.frappe.cloud")
        self.proxy.write_site_map_entry("10.0.0.1", "mysite.frappe.cloud")
        site_file = os.path.join(upstream_directory, "mysite.frappe.cloud")

        with patch.object(
            Proxy, "remove_site_from_upstream", new=Proxy.remove_site_from_upstream.__wrapped__
        ):
            self.proxy.remove_site_from_upstream(site_file)

        self.assertFalse(
            os.path.exists(os.path.join(self.proxy.upstream_map_directory, "mysite.frappe.cloud.map"))
        )

    def test_rewrite_socket_map_entries_for_upstream_updates_all_sites(self):
        self._add_site_file("10.0.0.1", "site-a.frappe.cloud")
        self._add_site_file("10.0.0.1", "site-b.frappe.cloud")
        self.proxy.write_site_map_entry("10.0.0.1", "site-a.frappe.cloud")
        self.proxy.write_site_map_entry("10.0.0.1", "site-b.frappe.cloud")

        os.makedirs(os.path.dirname(self.proxy.secondary_config_path), exist_ok=True)
        with open(self.proxy.secondary_config_path, "w") as f:
            json.dump({"10.0.0.1": [{"10.0.0.2": 1}]}, f)

        self.proxy._rewrite_socket_map_entries_for_upstream("10.0.0.1")

        hashed_upstream = sha(b"10.0.0.1").hexdigest()[:16]
        for site in ("site-a.frappe.cloud", "site-b.frappe.cloud"):
            socket_entry = self._read_map_entry(self.proxy.socket_map_directory, site)
            self.assertEqual(socket_entry, f"{site} http://{hashed_upstream}_primary;\n")

    def test_backfill_site_map_entries_creates_directories_with_zero_sites(self):
        self.proxy._backfill_site_map_entries()
        self.assertTrue(os.path.isdir(self.proxy.upstream_map_directory))
        self.assertTrue(os.path.isdir(self.proxy.socket_map_directory))

    def test_backfill_site_map_entries_covers_every_upstream(self):
        self._add_site_file("10.0.0.1", "site-a.frappe.cloud")
        self._add_site_file("10.0.0.2", "site-b.frappe.cloud")

        self.proxy._backfill_site_map_entries()

        self.assertTrue(
            os.path.exists(os.path.join(self.proxy.upstream_map_directory, "site-a.frappe.cloud.map"))
        )
        self.assertTrue(
            os.path.exists(os.path.join(self.proxy.upstream_map_directory, "site-b.frappe.cloud.map"))
        )

    def test_upstream_ips_excludes_site_lists(self):
        self._add_site_file("10.0.0.1", "site-a.frappe.cloud")

        upstream_ips = self.proxy.upstream_ips

        self.assertIn("10.0.0.1", upstream_ips)
        self.assertNotIn("sites", upstream_ips["10.0.0.1"])
        self.assertEqual(upstream_ips["10.0.0.1"]["hash"], sha(b"10.0.0.1").hexdigest()[:16])

    def test_amplify_dev_redirect_sites_returns_only_matching_upstream(self):
        # Finding an IP that actually hashes to the real constant isn't
        # feasible (sha512 preimage), so patch the constant to match a known
        # fixture IP's real hash instead of relying on a lucky collision.
        matching_ip = "10.0.0.1"
        matching_hash = sha(matching_ip.encode()).hexdigest()[:16]
        self._add_site_file(matching_ip, "redirect-site.frappe.cloud")
        self._add_site_file("10.0.0.99", "other-site.frappe.cloud")

        with patch("agent.proxy.AMPLIFY_DEV_REDIRECT_UPSTREAM_HASH", matching_hash):
            self.assertEqual(self.proxy.amplify_dev_redirect_sites, ["redirect-site.frappe.cloud"])

    def test_amplify_dev_redirect_sites_empty_when_no_match(self):
        self._add_site_file("10.0.0.99", "other-site.frappe.cloud")
        self.assertEqual(self.proxy.amplify_dev_redirect_sites, [])
