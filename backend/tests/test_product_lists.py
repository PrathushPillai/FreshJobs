from app.pipeline.product_lists import company_pages, markdown_sites, remoteintech_site, site_for_page


def test_site_for_page_takes_the_link_matching_the_slug():
    html = ('<a href="https://fonts.gstatic.com/x">f</a><a href="https://jobs.bvp.com/jobs">jobs</a>'
            '<a href="https://techcrunch.com/2024/typesafe-raises">news</a>'
            '<a href="https://www.sequoiacap.com/other">own</a><a href="https://www.typesafe.ai/">site</a>')
    assert site_for_page(html, "https://sequoiacap.com/companies/typesafe/") == "https://typesafe.ai"
    # Nothing matching the slug: no guess.
    assert site_for_page('<a href="https://other.com/">x</a>', "https://sequoiacap.com/companies/typesafe/") is None


def test_company_pages_and_nested_sitemaps():
    xml = ("<urlset><url><loc>https://lsvp.com/company/ladder/</loc></url>"
           "<url><loc>https://lsvp.com/company/</loc></url><url><loc>https://lsvp.com/news/x/</loc></url>"
           "<sitemap><loc>https://lsvp.com/company-sitemap2.xml</loc></sitemap></urlset>")
    assert company_pages(xml, "company") == (["https://lsvp.com/company/ladder/"], ["https://lsvp.com/company-sitemap2.xml"])


def test_markdown_sites_us_rows_only():
    md = "\n".join([
        "- [Aalyria](https://ats.rippling.com/aalyria-careers/jobs) | Remote | Timeboxed design exercise",
        "- [1000.software](https://www.1000.software/careers) | Krakow, Poland | Interviews",
        "- [Abstract](https://www.abstract.com) | San Francisco, CA",
        "- [How to hire](https://jes.al/post) by someone",
        "| [23andMe](https://www.23andme.com/careers/) | Mountain View, CA |",
    ])
    assert markdown_sites(md, us_only=True) == [
        ("Aalyria", "https://ats.rippling.com/aalyria-careers/jobs"), ("Abstract", "https://www.abstract.com"),
        ("23andMe", "https://www.23andme.com/careers/")]


def test_markdown_table_rows_without_place():
    md = "API Platform|[Firecamp](https://firecamp.dev/)|DX first devtool|<a href=https://github.com/x>|[Postman](https://www.postman.com/)"
    assert markdown_sites(md) == [("Firecamp", "https://firecamp.dev/")]


def test_remoteintech_region_filter():
    us = "---\ntitle: 10up\nwebsite: https://10up.com/\ncareers_url: https://10up.com/careers/\nregion: worldwide\n---\nText"
    eu = "---\ntitle: Euro Co\nwebsite: https://euro.example/\nregion: europe\n---\n"
    assert remoteintech_site(us) == ("10up", "https://10up.com/careers/")
    assert remoteintech_site(eu) is None
