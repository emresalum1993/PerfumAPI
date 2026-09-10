"""
FastAPI application for Perfume Data API.
Serves scraped perfume data from Supabase with authentication.
"""

from fastapi import FastAPI, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any, Union
from dotenv import load_dotenv
import os
import sys
import asyncio

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import (
    run_migration,
    insert_perfume,
    insert_perfumes_batch,
    get_all_perfumes,
    get_perfume_by_id,
    search_perfumes,
    get_perfume_count,
    upsert_reviews,
    get_reviews_by_perfume_id,
    get_reviews_count,
    supabase,
)
from utils.auth import get_current_user, verify_admin
from scraper.scrape import (
    scrape_fragrantica,
    scrape_fragrantica_by_brand,
    scrape_fragrantica_brands,
    scrape_fragrantica_by_url,
    scrape_fragrantica_reviews,
)
from pipeline import (
    check_unmapped,
    score_reviews,
    compute_moods,
    backfill_lexicon_scores,
    lexicon_check,
    score_opinions,
    generate_overview,
    get_ai_overview,
)
from pipeline.constants import DEFAULT_SCORE_LIMIT, MAX_SCORE_LIMIT
from pipeline.llm_client import LLMUnavailableError


# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(
    title="Perfume Data API",
    description="API for scraping and serving perfume data from Fragrantica",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configure CORS
# Allow frontend URL from environment variable
frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
allowed_origins = [
    frontend_url,
    "http://localhost:5173",  # Local development
    "http://localhost:3000", 
    "https://perfumapi-frontend.onrender.com" # Alternative local port
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models for request/response validation
class PerfumeBase(BaseModel):
    """Base perfume model"""
    name: str
    brand: Optional[str] = None
    release_year: Optional[int] = None
    gender: Optional[str] = None
    fragrantica_id: Optional[int] = None
    fragrance_family: Optional[str] = None
    perfumer: Optional[str] = None
    main_accords: List[str] = Field(default_factory=list)
    accord_breakdown: Optional[Dict[str, int]] = None
    notes_top: List[str] = Field(default_factory=list)
    notes_middle: List[str] = Field(default_factory=list)
    notes_base: List[str] = Field(default_factory=list)
    rating: Optional[float] = None
    votes: Optional[int] = None
    rating_breakdown: Optional[Dict[str, Any]] = None
    when_to_wear: Optional[Dict[str, Any]] = None
    longevity_breakdown: Optional[Dict[str, Any]] = None
    sillage_breakdown: Optional[Dict[str, Any]] = None
    price_value: Optional[Dict[str, Any]] = None
    gender_votes: Optional[Dict[str, Any]] = None
    ownership: Optional[Dict[str, Any]] = None
    pros: Optional[List[Dict[str, Any]]] = None
    cons: Optional[List[Dict[str, Any]]] = None
    similar_perfumes: Optional[List[Dict[str, Any]]] = None
    description: Optional[str] = None
    longevity: Optional[Union[str, float, int]] = None
    sillage: Optional[Union[str, float, int]] = None
    image_url: Optional[str] = None
    image_url_og: Optional[str] = None
    image_url_nobg: Optional[str] = None
    perfume_url: Optional[str] = None

    @field_validator(
        "main_accords",
        "notes_top",
        "notes_middle",
        "notes_base",
        mode="before",
    )
    @classmethod
    def empty_list_if_none(cls, value):
        """DB NULLs from older rows should serialize as []."""
        return value if value is not None else []


class PerfumeCreate(PerfumeBase):
    """Model for creating a new perfume"""
    pass


class PerfumeResponse(PerfumeBase):
    """Model for perfume response"""
    id: str
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class ScrapeRequest(BaseModel):
    """Model for scrape request"""
    limit: int = Field(default=2, ge=1, le=1000, description="Number of perfumes to scrape (1-1000)")


class ScrapeBrandRequest(BaseModel):
    """Model for brand scrape request"""
    brand_name: str = Field(..., description="Brand name (e.g., 'Jean Paul Gaultier')")
    limit: int = Field(default=10, ge=1, le=500, description="Number of perfumes to scrape from this brand (1-500)")


class ScrapeBrandsRequest(BaseModel):
    """Model for multiple brands scrape request"""
    brands: List[str] = Field(..., description="List of brand names")
    limit_per_brand: int = Field(default=10, ge=1, le=200, description="Number of perfumes to scrape per brand (1-200)")


class ScrapeUrlRequest(BaseModel):
    """Model for URL scrape request"""
    perfume_url: str = Field(..., description="Direct URL to a Fragrantica perfume page (e.g., 'https://www.fragrantica.com/perfume/Xerjoff/White-On-White-Three-76333.html')")


class FullProcessUrlRequest(BaseModel):
    """Model for single-call end-to-end perfume processing by URL or auto limit"""
    perfume_url: Optional[str] = Field(
        default=None,
        description="Direct Fragrantica URL (e.g., 'https://www.fragrantica.com/perfume/Maison-Francis-Kurkdjian/Baccarat-Rouge-540-33519.html')",
    )
    auto: bool = Field(
        default=False,
        description="If true, automatically discovers and processes popular perfumes using `limit` instead of `perfume_url`",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of popular perfumes to scrape and process when auto=true",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of popular perfumes to skip from the beginning when auto=true (for pagination)",
    )
    review_pages: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of review pages to scrape per sentiment (positive & negative)",
    )
    parallel_reviews: bool = Field(
        default=False,
        description="If true, scrape positive and negative reviews concurrently (pages within each sentiment stay sequential)",
    )
    parallel_perfumes: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Number of perfumes to process concurrently when auto=true (default 1)",
    )
    parallel_perfume_count: Optional[int] = Field(
        default=None,
        ge=1,
        le=10,
        description="Alias for parallel_perfumes",
    )
    force: bool = Field(
        default=True,
        description="Continue pipeline even if unmapped notes exist",
    )
    rescore: bool = Field(
        default=False,
        description="Wipe existing LLM scores for this perfume first",
    )


class FullProcessPopularRequest(BaseModel):
    """Model for automatic bulk end-to-end perfume processing (no URL needed)"""
    limit: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Number of popular perfumes to discover and run through the end-to-end master pipeline",
    )
    review_pages: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of review pages to scrape per sentiment (positive & negative)",
    )
    parallel_reviews: bool = Field(
        default=False,
        description="If true, scrape positive and negative reviews concurrently (pages within each sentiment stay sequential)",
    )
    force: bool = Field(
        default=True,
        description="Continue pipeline even if unmapped notes exist",
    )
    rescore: bool = Field(
        default=False,
        description="Wipe existing LLM scores for this perfume first",
    )


class ScrapeResponse(BaseModel):
    """Model for scrape response"""
    status: str
    message: str
    scraped_count: int
    inserted_count: int
    perfumes: List[Dict[str, Any]] = Field(default_factory=list)


class PerfumeListResponse(BaseModel):
    """Model for paginated perfume list response"""
    total: int
    limit: int
    offset: int
    perfumes: List[PerfumeResponse]


class ReviewResponse(BaseModel):
    """Stored Fragrantica review"""
    id: str
    perfume_id: str
    fragrantica_review_id: int
    fragrantica_perfume_id: Optional[int] = None
    sentiment: str
    username: Optional[str] = None
    user_id: Optional[int] = None
    content_html: Optional[str] = None
    content_text: Optional[str] = None
    vote_yes: Optional[int] = None
    vote_no: Optional[int] = None
    karma_score: Optional[float] = None
    review_date: Optional[str] = None
    perfume_votes: Optional[Dict[str, Any]] = None
    member_url: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class ReviewListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    perfume_id: str
    sentiment: Optional[str] = None
    reviews: List[ReviewResponse]


class ScrapeReviewsResponse(BaseModel):
    status: str
    message: str
    perfume_id: str
    fragrantica_id: int
    sentiment: str
    pages_requested: int
    pages_fetched: int
    has_more: bool
    scraped_count: int
    inserted_count: int
    reviews: List[Dict[str, Any]] = Field(default_factory=list)


# Startup event
@app.on_event("startup")
async def startup_event():
    """Run database migration on startup"""
    print("🚀 Starting Perfume API...")
    await run_migration()
    print("✅ API ready!")


# Health check endpoint
@app.get("/", tags=["Health"])
async def root():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "message": "Perfume API is running",
        "version": "1.0.0",
        "endpoints": {
            "docs": "/docs",
            "perfumes": "/perfumes",
            "scrape": "/scrape (auth required)",
            "scrape_brand": "/scrape/brand (auth required)",
            "scrape_brands": "/scrape/brands (auth required)",
            "scrape_url": "/scrape/url (auth required)",
            "scrape_reviews": "/scrape/reviews/{perfume_id}?pages=&sentiment= (auth required)",
            "perfume_reviews": "/perfumes/{perfume_id}/reviews",
            "perfume_moods": "/perfumes/{perfume_id}/moods",
            "perfume_summary": "/perfumes/{perfume_id}/summary",
            "perfume_lexicon_check": "/perfumes/{perfume_id}/lexicon-check",
            "pipeline_notes": "/pipeline/notes/check-unmapped (auth)",
            "pipeline_score": "/pipeline/reviews/score (auth)",
            "pipeline_score_lexicon": "/pipeline/reviews/score-lexicon (auth)",
            "pipeline_opinions": "/pipeline/opinions/score (auth)",
            "pipeline_moods": "/pipeline/moods/compute (auth)",
            "pipeline_ai_overview": "/pipeline/ai-overview/generate (auth)",
            "perfume_ai_overview": "/perfumes/{id}/ai-overview",
            "pipeline_run_all": "/pipeline/run-all (auth)",
            "pipeline_process_url": "/pipeline/process-url (auth)",
        }
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Detailed health check"""
    try:
        count = await get_perfume_count()
        return {
            "status": "healthy",
            "database": "connected",
            "perfumes_count": count
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")


# Public endpoints (no authentication required)
@app.get("/perfumes", response_model=PerfumeListResponse, tags=["Perfumes"])
async def list_perfumes(
    limit: int = Query(default=100, ge=1, le=500, description="Number of perfumes to return"),
    offset: int = Query(default=0, ge=0, description="Number of perfumes to skip")
):
    """
    Get list of all perfumes with pagination.
    
    - **limit**: Maximum number of perfumes to return (1-500)
    - **offset**: Number of perfumes to skip for pagination
    """
    try:
        perfumes = await get_all_perfumes(limit=limit, offset=offset)
        total = await get_perfume_count()
        
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "perfumes": perfumes
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching perfumes: {str(e)}")


@app.get("/perfumes/{perfume_id}", response_model=PerfumeResponse, tags=["Perfumes"])
async def get_perfume(perfume_id: str):
    """
    Get a specific perfume by ID.
    
    - **perfume_id**: UUID of the perfume
    """
    try:
        perfume = await get_perfume_by_id(perfume_id)
        
        if not perfume:
            raise HTTPException(status_code=404, detail=f"Perfume with ID {perfume_id} not found")
        
        return perfume
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching perfume: {str(e)}")


@app.get("/perfumes/{perfume_id}/reviews", response_model=ReviewListResponse, tags=["Reviews"])
async def list_perfume_reviews(
    perfume_id: str,
    sentiment: Optional[str] = Query(
        default=None,
        pattern="^(positive|negative)$",
        description="Optional filter: positive or negative",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    List stored reviews for a perfume (by our DB UUID).
    """
    perfume = await get_perfume_by_id(perfume_id)
    if not perfume:
        raise HTTPException(status_code=404, detail=f"Perfume with ID {perfume_id} not found")

    reviews = await get_reviews_by_perfume_id(
        perfume_id,
        sentiment=sentiment,
        limit=limit,
        offset=offset,
    )
    total = await get_reviews_count(perfume_id, sentiment=sentiment)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "perfume_id": perfume_id,
        "sentiment": sentiment,
        "reviews": reviews,
    }


@app.get("/perfumes/search/{query}", response_model=List[PerfumeResponse], tags=["Perfumes"])
async def search_perfumes_endpoint(
    query: str,
    limit: int = Query(default=50, ge=1, le=200, description="Maximum number of results")
):
    """
    Search perfumes by name or brand.
    
    - **query**: Search term (searches in name and brand)
    - **limit**: Maximum number of results to return
    """
    try:
        perfumes = await search_perfumes(query, limit=limit)
        return perfumes
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error searching perfumes: {str(e)}")


# Protected endpoints (authentication required)
@app.post("/perfumes", response_model=PerfumeResponse, tags=["Perfumes (Auth Required)"])
async def create_perfume(
    perfume: PerfumeCreate,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Create a new perfume entry manually.
    
    **Requires authentication**: Include `Authorization: Bearer <token>` header.
    """
    try:
        perfume_dict = perfume.model_dump()
        result = await insert_perfume(perfume_dict)
        
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create perfume")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating perfume: {str(e)}")


@app.post("/scrape", response_model=ScrapeResponse, tags=["Scraper (Auth Required)"])
async def scrape_perfumes(
    scrape_request: ScrapeRequest,
    current_user: Dict[str, Any] = Depends(verify_admin)
):
    """
    Trigger perfume scraping from Fragrantica.
    
    **Requires authentication**: Include `Authorization: Bearer <token>` header.
    
    - **limit**: Number of perfumes to scrape (default: 2, max: 1000)
    
    This endpoint will:
    1. Scrape perfume data from Fragrantica
    2. Save the data to data.json
    3. Insert the data into Supabase database
    
    ⚠️ **Warning**: Scraping large numbers may take time. Start with small limits for testing.
    """
    try:
        limit = scrape_request.limit
        
        print(f"🔍 Starting scrape for {limit} perfumes (requested by user {current_user.get('id', 'unknown')})")
        
        # Run the scraper in a thread to avoid blocking the event loop
        perfumes = await asyncio.to_thread(scrape_fragrantica, limit=limit)
        
        if not perfumes:
            return {
                "status": "warning",
                "message": "No perfumes were scraped",
                "scraped_count": 0,
                "inserted_count": 0,
                "perfumes": []
            }
        
        # Insert into database
        inserted_count = await insert_perfumes_batch(perfumes)
        
        return {
            "status": "success",
            "message": f"Successfully scraped and stored perfumes",
            "scraped_count": len(perfumes),
            "inserted_count": inserted_count,
            "perfumes": perfumes[:5]  # Return first 5 as preview
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error during scraping: {str(e)}"
        )


@app.post("/scrape/brand", response_model=ScrapeResponse, tags=["Scraper (Auth Required)"])
async def scrape_brand(
    scrape_request: ScrapeBrandRequest,
    current_user: Dict[str, Any] = Depends(verify_admin)
):
    """
    Scrape perfumes from a specific brand on Fragrantica.
    
    **Requires authentication**: Include `Authorization: Bearer <token>` header.
    
    - **brand_name**: Name of the brand (e.g., "Jean Paul Gaultier", "Xerjoff", "Creed")
    - **limit**: Number of perfumes to scrape from this brand (default: 10, max: 500)
    
    This endpoint will:
    1. Scrape perfume data for the specified brand from Fragrantica
    2. Save the data to data.json
    3. Insert the data into Supabase database
    
    ⚠️ **Warning**: Scraping may take time depending on the limit. Start with small limits for testing.
    """
    try:
        brand_name = scrape_request.brand_name
        limit = scrape_request.limit
        
        print(f"🔍 Starting brand scrape for '{brand_name}' with limit {limit} (requested by user {current_user.get('id', 'unknown')})")
        
        # Run the brand scraper in a thread to avoid blocking the event loop
        perfumes = await asyncio.to_thread(scrape_fragrantica_by_brand, brand_name, limit=limit)
        
        if not perfumes:
            return {
                "status": "warning",
                "message": f"No perfumes were scraped for brand '{brand_name}'",
                "scraped_count": 0,
                "inserted_count": 0,
                "perfumes": []
            }
        
        # Insert into database
        inserted_count = await insert_perfumes_batch(perfumes)
        
        return {
            "status": "success",
            "message": f"Successfully scraped {len(perfumes)} perfumes from '{brand_name}'",
            "scraped_count": len(perfumes),
            "inserted_count": inserted_count,
            "perfumes": perfumes[:5]  # Return first 5 as preview
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error during brand scraping: {str(e)}"
        )


@app.post("/scrape/brands", response_model=ScrapeResponse, tags=["Scraper (Auth Required)"])
async def scrape_multiple_brands(
    scrape_request: ScrapeBrandsRequest,
    current_user: Dict[str, Any] = Depends(verify_admin)
):
    """
    Scrape perfumes from multiple brands on Fragrantica.
    
    **Requires authentication**: Include `Authorization: Bearer <token>` header.
    
    - **brands**: List of brand names (e.g., ["Jean Paul Gaultier", "Xerjoff", "Creed"])
    - **limit_per_brand**: Number of perfumes to scrape per brand (default: 10, max: 200)
    
    This endpoint will:
    1. Scrape perfume data for all specified brands from Fragrantica
    2. Save the combined data to data.json
    3. Insert all data into Supabase database
    
    ⚠️ **Warning**: Scraping multiple brands may take significant time. 
    The total scraping time = (number of brands) × (limit per brand) × (time per perfume).
    """
    try:
        brands = scrape_request.brands
        limit_per_brand = scrape_request.limit_per_brand
        
        if not brands:
            return {
                "status": "error",
                "message": "No brands provided",
                "scraped_count": 0,
                "inserted_count": 0,
                "perfumes": []
            }
        
        print(f"🔍 Starting multi-brand scrape for {len(brands)} brands with {limit_per_brand} perfumes each (requested by user {current_user.get('id', 'unknown')})")
        print(f"📋 Brands: {', '.join(brands)}")
        
        # Run the multi-brand scraper in a thread to avoid blocking the event loop
        perfumes = await asyncio.to_thread(scrape_fragrantica_brands, brands, limit_per_brand=limit_per_brand)
        
        if not perfumes:
            return {
                "status": "warning",
                "message": f"No perfumes were scraped from the specified brands",
                "scraped_count": 0,
                "inserted_count": 0,
                "perfumes": []
            }
        
        # Insert into database
        inserted_count = await insert_perfumes_batch(perfumes)
        
        return {
            "status": "success",
            "message": f"Successfully scraped {len(perfumes)} perfumes from {len(brands)} brands",
            "scraped_count": len(perfumes),
            "inserted_count": inserted_count,
            "perfumes": perfumes[:5]  # Return first 5 as preview
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error during multi-brand scraping: {str(e)}"
        )


@app.post("/scrape/url", response_model=ScrapeResponse, tags=["Scraper (Auth Required)"])
async def scrape_by_url(
    scrape_request: ScrapeUrlRequest,
    current_user: Dict[str, Any] = Depends(verify_admin)
):
    """
    Scrape a specific perfume by its direct Fragrantica URL.
    
    **Requires authentication**: Include `Authorization: Bearer <token>` header.
    
    - **perfume_url**: Direct URL to a Fragrantica perfume page 
      (e.g., "https://www.fragrantica.com/perfume/Xerjoff/White-On-White-Three-76333.html")
    
    This endpoint will:
    1. Scrape perfume data from the specified URL
    2. Save the data to data.json
    3. Insert the data into Supabase database
    
    ⚠️ **Note**: This is the fastest scraping method as it only fetches one perfume page.
    """
    try:
        perfume_url = scrape_request.perfume_url
        
        # Validate URL
        if not perfume_url or 'fragrantica.com/perfume/' not in perfume_url:
            return {
                "status": "error",
                "message": f"Invalid Fragrantica perfume URL: {perfume_url}",
                "scraped_count": 0,
                "inserted_count": 0,
                "perfumes": []
            }
        
        print(f"🔍 Starting URL scrape for '{perfume_url}' (requested by user {current_user.get('id', 'unknown')})")
        
        # Run the URL scraper in a thread to avoid blocking the event loop
        perfume = await asyncio.to_thread(scrape_fragrantica_by_url, perfume_url)
        
        if not perfume:
            return {
                "status": "error",
                "message": f"Failed to scrape perfume from URL: {perfume_url}",
                "scraped_count": 0,
                "inserted_count": 0,
                "perfumes": []
            }
        
        # Insert into database
        inserted_count = await insert_perfumes_batch([perfume])
        
        return {
            "status": "success",
            "message": f"Successfully scraped '{perfume.get('name', 'Unknown')}' by {perfume.get('brand', 'Unknown')}",
            "scraped_count": 1,
            "inserted_count": inserted_count,
            "perfumes": [perfume]  # Return the scraped perfume
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error during URL scraping: {str(e)}"
        )


@app.post(
    "/scrape/reviews/{perfume_id}",
    response_model=ScrapeReviewsResponse,
    tags=["Scraper (Auth Required)"],
)
async def scrape_perfume_reviews(
    perfume_id: str,
    pages: int = Query(
        default=5,
        ge=1,
        le=20,
        description="Number of review pages to fetch (default 5; Fragrantica often caps at 5)",
    ),
    sentiment: str = Query(
        default="positive",
        pattern="^(positive|negative)$",
        description="Review sentiment filter: positive or negative",
    ),
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """
    Scrape Fragrantica reviews for a perfume already in the database.

    - **perfume_id**: Our DB UUID (must have `fragrantica_id` and `perfume_url`)
    - **pages**: Max AJAX pages to follow via next_token (default: 5)
    - **sentiment**: `positive` or `negative` (default: positive)

    Decrypts CryptoJS `{ct,iv,s}` responses and upserts into `reviews`.
    """
    perfume = await get_perfume_by_id(perfume_id)
    if not perfume:
        raise HTTPException(status_code=404, detail=f"Perfume with ID {perfume_id} not found")

    fragrantica_id = perfume.get("fragrantica_id")
    perfume_url = perfume.get("perfume_url")
    if not fragrantica_id:
        raise HTTPException(
            status_code=400,
            detail="Perfume is missing fragrantica_id; scrape the perfume page first",
        )
    if not perfume_url:
        raise HTTPException(
            status_code=400,
            detail="Perfume is missing perfume_url; needed as AJAX referer",
        )

    print(
        f"📝 Review scrape perfume={perfume_id} fragrantica_id={fragrantica_id} "
        f"pages={pages} sentiment={sentiment} user={current_user.get('id', 'unknown')}"
    )

    try:
        result = await asyncio.to_thread(
            scrape_fragrantica_reviews,
            perfume_uuid=perfume_id,
            fragrantica_id=int(fragrantica_id),
            perfume_url=perfume_url,
            sentiment=sentiment,
            pages=pages,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scraping reviews: {str(e)}")

    reviews = result.get("reviews") or []
    inserted_count = await upsert_reviews(reviews)

    return {
        "status": "success" if reviews else "error",
        "message": (
            f"Scraped {len(reviews)} {sentiment} reviews "
            f"({result.get('pages_fetched', 0)}/{pages} pages)"
            if reviews
            else "No reviews scraped"
        ),
        "perfume_id": perfume_id,
        "fragrantica_id": int(fragrantica_id),
        "sentiment": sentiment,
        "pages_requested": pages,
        "pages_fetched": int(result.get("pages_fetched") or 0),
        "has_more": bool(result.get("has_more")),
        "scraped_count": len(reviews),
        "inserted_count": inserted_count,
        "reviews": reviews,
    }


@app.get("/perfumes/{perfume_id}/moods", tags=["Moods"])
async def get_perfume_moods(perfume_id: str):
    """Public: stored perfume_mood_scores for one perfume."""
    perfume = await get_perfume_by_id(perfume_id)
    if not perfume:
        raise HTTPException(status_code=404, detail=f"Perfume with ID {perfume_id} not found")
    try:
        response = (
            supabase.table("perfume_mood_scores")
            .select("*")
            .eq("perfume_id", perfume_id)
            .order("score", desc=True)
            .execute()
        )
        return {
            "perfume_id": perfume_id,
            "moods": response.data or [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching moods: {str(e)}")


@app.get("/perfumes/{perfume_id}/summary", tags=["Perfumes"])
async def get_perfume_summary(perfume_id: str):
    """
    Public compact card: identity + top accords + computed mood snapshot.
    Omits full notes, reviews, similar list, and raw breakdowns.
    """
    perfume = await get_perfume_by_id(perfume_id)
    if not perfume:
        raise HTTPException(status_code=404, detail=f"Perfume with ID {perfume_id} not found")

    try:
        mood_resp = (
            supabase.table("perfume_mood_scores")
            .select("mood_key,score,sample_size,confidence,gated_out,computed_at")
            .eq("perfume_id", perfume_id)
            .order("score", desc=True)
            .execute()
        )
        mood_rows = mood_resp.data or []

        breakdown = perfume.get("accord_breakdown") or {}
        if isinstance(breakdown, dict) and breakdown:
            top_accords = [
                {"name": name, "pct": int(pct)}
                for name, pct in sorted(
                    breakdown.items(),
                    key=lambda item: int(item[1]) if item[1] is not None else 0,
                    reverse=True,
                )[:5]
            ]
        else:
            top_accords = [
                {"name": name, "pct": None}
                for name in (perfume.get("main_accords") or [])[:5]
            ]

        moods = [
            {
                "mood_key": row["mood_key"],
                "score": float(row["score"]) if row.get("score") is not None else None,
                "confidence": row.get("confidence"),
                "gated_out": bool(row.get("gated_out")),
            }
            for row in mood_rows
        ]
        active = [m for m in moods if not m["gated_out"]]
        top_mood = (active or moods)[0] if moods else None

        sample_size = mood_rows[0].get("sample_size") if mood_rows else 0
        confidence = mood_rows[0].get("confidence") if mood_rows else None
        gated_out = bool(mood_rows[0].get("gated_out")) if mood_rows else False
        computed_at = mood_rows[0].get("computed_at") if mood_rows else None

        reviews_total = await get_reviews_count(perfume_id)

        return {
            "perfume_id": perfume_id,
            "name": perfume.get("name"),
            "brand": perfume.get("brand"),
            "gender": perfume.get("gender"),
            "release_year": perfume.get("release_year"),
            "image_url": perfume.get("image_url"),
            "perfume_url": perfume.get("perfume_url"),
            "rating": perfume.get("rating"),
            "votes": perfume.get("votes"),
            "longevity": perfume.get("longevity"),
            "sillage": perfume.get("sillage"),
            "top_accords": top_accords,
            "top_mood": top_mood,
            "moods": moods,
            "sample_size": sample_size,
            "confidence": confidence,
            "gated_out": gated_out,
            "computed_at": computed_at,
            "reviews_total": reviews_total,
            "has_moods": bool(moods),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching summary: {str(e)}")


@app.get("/perfumes/{perfume_id}/lexicon-check", tags=["Moods"])
async def get_perfume_lexicon_check(perfume_id: str):
    """
    Public transparency: LLM vs NRC-VAD lexicon vs blended valence/dominance.
    """
    perfume = await get_perfume_by_id(perfume_id)
    if not perfume:
        raise HTTPException(status_code=404, detail=f"Perfume with ID {perfume_id} not found")
    try:
        return await asyncio.to_thread(lexicon_check, perfume_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lexicon check failed: {str(e)}")


@app.post("/pipeline/notes/check-unmapped", tags=["Pipeline (Auth Required)"])
async def pipeline_check_unmapped(
    perfume_id: Optional[str] = Query(default=None, description="Optional perfume UUID"),
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """List accord/note raw texts not present in note_aliases."""
    try:
        result = await asyncio.to_thread(check_unmapped, perfume_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unmapped check failed: {str(e)}")


@app.post("/pipeline/reviews/score", tags=["Pipeline (Auth Required)"])
async def pipeline_score_reviews(
    perfume_id: Optional[str] = Query(default=None),
    limit: int = Query(default=DEFAULT_SCORE_LIMIT, ge=1, le=MAX_SCORE_LIMIT),
    rescore: bool = Query(
        default=False,
        description="Delete existing LLM+lexicon scores for perfume_id, then re-score (requires perfume_id)",
    ),
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """
    Score unscored reviews via OmniRoute LLM (8 axes/gates) and NRC-VAD lexicon
    (valence + dominance when matched_words >= threshold).
    With rescore=true + perfume_id: wipe that perfume's llm+lexicon scores first.
    """
    if rescore and not perfume_id:
        raise HTTPException(
            status_code=400,
            detail="rescore=true requires perfume_id (refuses global score wipe)",
        )
    try:
        result = await asyncio.to_thread(
            score_reviews,
            perfume_id=perfume_id,
            limit=limit,
            rescore=rescore,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Review scoring failed: {str(e)}")


@app.post("/pipeline/reviews/score-lexicon", tags=["Pipeline (Auth Required)"])
async def pipeline_score_lexicon(
    perfume_id: Optional[str] = Query(default=None),
    limit: int = Query(default=DEFAULT_SCORE_LIMIT, ge=1, le=MAX_SCORE_LIMIT),
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """
    Cheap backfill: add method=lexicon valence/dominance for reviews that already
    have a full LLM score set. Does not call the LLM.
    """
    try:
        result = await asyncio.to_thread(
            backfill_lexicon_scores,
            perfume_id=perfume_id,
            limit=limit,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lexicon backfill failed: {str(e)}")


@app.post("/pipeline/opinions/score", tags=["Pipeline (Auth Required)"])
async def pipeline_score_opinions(
    perfume_id: str = Query(..., description="Perfume UUID (required)"),
    rescore: bool = Query(
        default=False,
        description="Delete existing opinion scores for perfume, then re-score",
    ),
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """
    Score Fragrantica pros/cons via LLM (+ lexicon for V/D).
    Skips opinions with character_relevant=false (price, longevity, etc.).
    Not included in run-all — call explicitly after scrape.
    """
    try:
        result = await asyncio.to_thread(
            score_opinions,
            perfume_id=perfume_id,
            rescore=rescore,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Opinion scoring failed: {str(e)}")


@app.post("/pipeline/moods/compute", tags=["Pipeline (Auth Required)"])
async def pipeline_compute_moods(
    perfume_id: Optional[str] = Query(default=None),
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """Blend note priors + review posteriors, apply quality_gates, upsert perfume_mood_scores."""
    try:
        result = await asyncio.to_thread(compute_moods, perfume_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mood compute failed: {str(e)}")


@app.get("/perfumes/{perfume_id}/ai-overview", tags=["Perfumes"])
async def perfume_ai_overview(perfume_id: str):
    """
    Public cached AI overview. Never triggers LLM generation.
    Returns {"generated": false} when no row exists.
    """
    perfume = await get_perfume_by_id(perfume_id)
    if not perfume:
        raise HTTPException(status_code=404, detail=f"Perfume with ID {perfume_id} not found")
    try:
        return await asyncio.to_thread(get_ai_overview, perfume_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching AI overview: {str(e)}")


@app.post("/pipeline/ai-overview/generate", tags=["Pipeline (Auth Required)"])
async def pipeline_generate_ai_overview(
    perfume_id: str = Query(..., description="Perfume UUID (required)"),
    force: bool = Query(
        default=False,
        description="Regenerate even if a row already exists (costs an LLM call)",
    ),
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """
    Generate original summary + pros/cons chips + fuller pros_list/cons_list.
    Skips LLM when a row exists and force=false. Not included in run-all.
    """
    perfume = await get_perfume_by_id(perfume_id)
    if not perfume:
        raise HTTPException(status_code=404, detail=f"Perfume with ID {perfume_id} not found")
    try:
        return await asyncio.to_thread(generate_overview, perfume_id, force=force)
    except ValueError as e:
        msg = str(e)
        code = 422 if msg.startswith("insufficient_data") else 400
        raise HTTPException(status_code=code, detail=msg)
    except LLMUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI overview generate failed: {str(e)}")


@app.post("/pipeline/run-all", tags=["Pipeline (Auth Required)"])
async def pipeline_run_all(
    perfume_id: Optional[str] = Query(default=None),
    force: bool = Query(default=False, description="Continue even if unmapped notes exist"),
    rescore: bool = Query(
        default=False,
        description="Wipe LLM scores for perfume_id then re-score (requires perfume_id)",
    ),
    score_limit: int = Query(default=DEFAULT_SCORE_LIMIT, ge=1, le=MAX_SCORE_LIMIT),
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """Orchestrator: check-unmapped → score → compute."""
    if rescore and not perfume_id:
        raise HTTPException(
            status_code=400,
            detail="rescore=true requires perfume_id (refuses global score wipe)",
        )

    notes_result = await asyncio.to_thread(check_unmapped, perfume_id)
    if notes_result.get("count", 0) > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Unmapped note/accord texts found; fix note_aliases or pass force=true",
                "unmapped": notes_result.get("unmapped"),
                "count": notes_result.get("count"),
            },
        )

    try:
        score_result = await asyncio.to_thread(
            score_reviews,
            perfume_id=perfume_id,
            limit=score_limit,
            rescore=rescore,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    mood_result = await asyncio.to_thread(compute_moods, perfume_id)
    return {
        "status": "success",
        "force": force,
        "rescore": rescore,
        "notes": notes_result,
        "score": score_result,
        "moods": mood_result,
    }


async def _scrape_one_sentiment_reviews(
    *,
    perfume_id: str,
    fragrantica_id: int,
    perfume_url: str,
    sentiment: str,
    review_pages: int,
    max_retries: int = 3,
) -> tuple[str, int, int]:
    """Scrape one sentiment with retries. Returns (sentiment, review_count, inserted_count)."""
    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            res = await asyncio.to_thread(
                scrape_fragrantica_reviews,
                perfume_uuid=perfume_id,
                fragrantica_id=fragrantica_id,
                perfume_url=perfume_url,
                sentiment=sentiment,
                pages=review_pages,
            )
            reviews = res.get("reviews") or []
            inserted = await upsert_reviews(reviews)
            print(
                f"   -> Scraped {len(reviews)} {sentiment} reviews (inserted/updated: {inserted})",
                flush=True,
            )
            return sentiment, len(reviews), inserted
        except Exception as e:
            last_error = e
            print(
                f"   ⚠️ [Attempt {attempt}/{max_retries}] Review scraping ({sentiment}) failed: {e}",
                flush=True,
            )
            if attempt < max_retries:
                print(f"   🔄 Retrying {sentiment} review scrape in {5 * attempt}s...", flush=True)
                await asyncio.sleep(5 * attempt)

    raise HTTPException(
        status_code=502,
        detail=f"Failed to scrape {sentiment} reviews for perfume after {max_retries} attempts: {last_error}",
    )


async def _scrape_perfume_reviews_step(
    *,
    perfume_id: str,
    fragrantica_id: int,
    perfume_url: str,
    review_pages: int,
    parallel_reviews: bool,
) -> int:
    inserted_total = 0
    if parallel_reviews:
        print("📝 [2/5] Scraping reviews in parallel (positive & negative)...", flush=True)
        results = await asyncio.gather(
            _scrape_one_sentiment_reviews(
                perfume_id=perfume_id,
                fragrantica_id=fragrantica_id,
                perfume_url=perfume_url,
                sentiment="positive",
                review_pages=review_pages,
            ),
            _scrape_one_sentiment_reviews(
                perfume_id=perfume_id,
                fragrantica_id=fragrantica_id,
                perfume_url=perfume_url,
                sentiment="negative",
                review_pages=review_pages,
            ),
        )
        inserted_total = sum(r[2] for r in results)
    else:
        print("📝 [2/5] Scraping reviews (positive & negative pages)...", flush=True)
        for sentiment in ("positive", "negative"):
            _, _, inserted = await _scrape_one_sentiment_reviews(
                perfume_id=perfume_id,
                fragrantica_id=fragrantica_id,
                perfume_url=perfume_url,
                sentiment=sentiment,
                review_pages=review_pages,
            )
            inserted_total += inserted

    print(f"✅ [2/5] Finished reviews scrape. Total new reviews added: {inserted_total}", flush=True)
    return inserted_total


async def _execute_master_pipeline(
    perfume_url: str,
    review_pages: int = 5,
    force: bool = True,
    rescore: bool = False,
    parallel_reviews: bool = False,
):
    perfume_url = perfume_url.strip()
    if not perfume_url or "fragrantica.com/perfume/" not in perfume_url:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid Fragrantica perfume URL: {perfume_url}",
        )

    # Step 1: Scrape Perfume
    print(f"\n==================================================", flush=True)
    print(f"🔍 [1/5] Scraping perfume details for: '{perfume_url}'...", flush=True)
    scraped_perfume = await asyncio.to_thread(scrape_fragrantica_by_url, perfume_url)
    if not scraped_perfume:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to scrape perfume from URL: {perfume_url}",
        )

    await insert_perfumes_batch([scraped_perfume])

    # Fetch inserted perfume row to get UUID
    perfume_resp = (
        supabase.table("perfumes")
        .select("*")
        .eq("perfume_url", perfume_url)
        .limit(1)
        .execute()
    )
    if not perfume_resp.data:
        raise HTTPException(status_code=500, detail="Failed to retrieve perfume UUID after insert")

    perfume = perfume_resp.data[0]
    perfume_id = perfume["id"]
    fragrantica_id = perfume.get("fragrantica_id")
    print(f"✅ [1/5] Perfume Scraped & Saved: '{perfume.get('name')}' by '{perfume.get('brand')}' (ID: {perfume_id})", flush=True)

    # Step 2: Scrape Reviews (Positive & Negative)
    reviews_scraped_count = 0
    if fragrantica_id:
        reviews_scraped_count = await _scrape_perfume_reviews_step(
            perfume_id=perfume_id,
            fragrantica_id=int(fragrantica_id),
            perfume_url=perfume_url,
            review_pages=review_pages,
            parallel_reviews=parallel_reviews,
        )

    # Step 3: Check notes & Score Reviews (Batched LLM)
    print(f"🧠 [3/5] Scoring reviews with batched LLM & checking unmapped notes...", flush=True)
    notes_result = await asyncio.to_thread(check_unmapped, perfume_id)
    if notes_result.get("count", 0) > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Unmapped notes found; pass force=true to proceed",
                "unmapped": notes_result.get("unmapped"),
            },
        )

    score_result = await asyncio.to_thread(
        score_reviews,
        perfume_id=perfume_id,
        limit=MAX_SCORE_LIMIT,
        rescore=rescore,
    )
    print(f"✅ [3/5] Review scoring finished: {score_result.get('scored')} reviews scored via LLM, {score_result.get('lexicon_written')} lexicon scores persisted.", flush=True)

    # Step 4: Score Opinions (Pros/Cons Batched LLM) & Compute Moods
    print(f"📊 [4/5] Scoring pros/cons opinions & computing mood scores...", flush=True)
    opinion_result = await asyncio.to_thread(
        score_opinions,
        perfume_id=perfume_id,
        rescore=rescore,
    )
    mood_result = await asyncio.to_thread(compute_moods, perfume_id)
    print(f"✅ [4/5] Mood computation finished: {opinion_result.get('scored')} opinions scored, updated {mood_result.get('updated_perfumes')} perfume mood scores.", flush=True)

    # Step 5: Generate AI Overview
    print(f"✨ [5/5] Synthesizing AI Overview (summary + pros/cons chips)...", flush=True)
    ai_overview_result = None
    try:
        ai_overview_result = await asyncio.to_thread(
            generate_overview,
            perfume_id,
            force=force,
        )
        print(f"✅ [5/5] AI Overview synthesis complete!", flush=True)
    except Exception as e:
        ai_overview_result = {"warning": f"AI overview generation skipped: {str(e)}"}
        print(f"⚠️ [5/5] AI Overview skipped: {e}", flush=True)

    print(f"🎉 MASTER PIPELINE COMPLETED SUCCESSFULLY FOR '{perfume.get('name')}'!", flush=True)
    print(f"==================================================\n", flush=True)

    return {
        "status": "success",
        "perfume_id": perfume_id,
        "perfume": perfume,
        "reviews_scraped": reviews_scraped_count,
        "notes": notes_result,
        "review_scores": score_result,
        "opinion_scores": opinion_result,
        "moods": mood_result,
        "ai_overview": ai_overview_result,
    }


@app.post("/pipeline/process-url", tags=["Pipeline (Auth Required)"])
async def pipeline_process_url(
    request: FullProcessUrlRequest,
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """
    End-to-End Master Pipeline for perfume processing.

    Modes:
    1. Single URL Mode: Pass `perfume_url` (e.g. `{"perfume_url": "https://...", "review_pages": 5, "force": true}`)
    2. Auto Limit Mode: Pass `auto: true`, `limit`, and optional `offset` (e.g. `{"auto": true, "limit": 10, "offset": 10, "review_pages": 5, "force": true}`)

    In ONE request, this endpoint will run all 5 steps for the perfume(s):
    1. Scrape perfume details (notes, accords, pros/cons)
    2. Scrape reviews (positive AND negative pages)
    3. Run note checking & batched review scoring
    4. Run character-relevant opinion scoring (pros/cons)
    5. Compute deterministic mood scores
    6. Generate AI overview summary & pros/cons lists
    """
    # Check if auto mode is requested or perfume_url is omitted
    if request.auto or not request.perfume_url:
        from scraper.scrape import FragranticaScraper

        limit = request.limit or 10
        offset = request.offset or 0
        concurrency = request.parallel_perfumes
        if request.parallel_perfume_count is not None:
            concurrency = request.parallel_perfume_count

        print(f"\n🚀 [AUTOMATIC BULK PIPELINE] Discovering popular perfumes from Fragrantica (limit={limit}, offset={offset}, parallel_perfumes={concurrency})...", flush=True)

        scraper = FragranticaScraper(delay=17.0)
        urls = await asyncio.to_thread(scraper.get_popular_perfumes_urls, limit=limit, offset=offset)

        if not urls:
            return {
                "status": "warning",
                "message": "No perfume URLs found from Fragrantica search",
                "processed_count": 0,
                "results": [],
            }

        print(f"Found {len(urls)} popular perfume URLs to process (concurrency={concurrency}). Beginning master pipeline...\n", flush=True)

        sem = asyncio.Semaphore(concurrency)

        async def _process_single(idx: int, url: str):
            async with sem:
                print(f"▶️ Processing perfume [{idx}/{len(urls)}]: {url}", flush=True)
                try:
                    res = await _execute_master_pipeline(
                        perfume_url=url,
                        review_pages=request.review_pages,
                        force=request.force,
                        rescore=request.rescore,
                        parallel_reviews=request.parallel_reviews,
                    )
                    return {
                        "url": url,
                        "status": "success",
                        "perfume_id": res.get("perfume_id"),
                        "perfume_name": res.get("perfume", {}).get("name"),
                    }
                except Exception as e:
                    print(f"❌ Failed processing {url}: {e}", flush=True)
                    return {
                        "url": url,
                        "status": "error",
                        "error": str(e),
                    }

        tasks = [_process_single(idx, url) for idx, url in enumerate(urls, 1)]
        results = await asyncio.gather(*tasks)

        return {
            "status": "success",
            "mode": "auto",
            "requested_limit": limit,
            "requested_offset": offset,
            "parallel_perfumes": concurrency,
            "found_urls": len(urls),
            "successful_count": sum(1 for r in results if r["status"] == "success"),
            "results": list(results),
        }

    # Otherwise, Single URL Mode
    perfume_url = request.perfume_url.strip()
    if not perfume_url or "fragrantica.com/perfume/" not in perfume_url:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid Fragrantica perfume URL: {perfume_url}",
        )

    return await _execute_master_pipeline(
        perfume_url=perfume_url,
        review_pages=request.review_pages,
        force=request.force,
        rescore=request.rescore,
        parallel_reviews=request.parallel_reviews,
    )


@app.post("/pipeline/process-popular", tags=["Pipeline (Auth Required)"])
async def pipeline_process_popular(
    request: FullProcessPopularRequest,
    current_user: Dict[str, Any] = Depends(verify_admin),
):
    """
    Automatic End-to-End Master Pipeline for popular perfumes (NO URL NEEDED).

    Discovers up to `limit` popular perfumes from Fragrantica automatically,
    and runs the full 5-step Master Pipeline for each one sequentially!
    """
    from scraper.scrape import FragranticaScraper

    limit = request.limit
    print(f"\n🚀 [AUTOMATIC BULK PIPELINE] Discovering top {limit} popular perfumes from Fragrantica...", flush=True)

    scraper = FragranticaScraper(delay=17.0)
    urls = await asyncio.to_thread(scraper.get_popular_perfumes_urls, limit=limit)

    if not urls:
        return {
            "status": "warning",
            "message": "No perfume URLs found from Fragrantica search",
            "processed_count": 0,
            "results": [],
        }

    print(f"Found {len(urls)} popular perfume URLs to process. Beginning sequential master pipeline...\n", flush=True)

    results = []
    for idx, url in enumerate(urls, 1):
        print(f"▶️ Processing perfume [{idx}/{len(urls)}]: {url}", flush=True)
        try:
            res = await _execute_master_pipeline(
                perfume_url=url,
                review_pages=request.review_pages,
                force=request.force,
                rescore=request.rescore,
                parallel_reviews=request.parallel_reviews,
            )
            results.append({
                "url": url,
                "status": "success",
                "perfume_id": res.get("perfume_id"),
                "perfume_name": res.get("perfume", {}).get("name"),
            })
        except Exception as e:
            print(f"❌ Failed processing {url}: {e}", flush=True)
            results.append({
                "url": url,
                "status": "error",
                "error": str(e),
            })

    return {
        "status": "success",
        "requested_limit": limit,
        "found_urls": len(urls),
        "successful_count": sum(1 for r in results if r["status"] == "success"),
        "results": results,
    }


# Statistics endpoint (public)
@app.get("/stats", tags=["Statistics"])
async def get_stats():
    """
    Get database statistics.
    """
    try:
        total_perfumes = await get_perfume_count()
        
        return {
            "total_perfumes": total_perfumes,
            "database": "Supabase PostgreSQL",
            "source": "Fragrantica.com"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching stats: {str(e)}")


# Error handlers
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Custom 404 handler"""
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not Found",
            "message": "The requested resource was not found",
            "path": str(request.url),
        },
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    """Custom 500 handler"""
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred",
            "path": str(request.url),
        },
    )


if __name__ == "__main__":
    import uvicorn
    
    # Run the server
    port = int(os.getenv("PORT", 9000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )

