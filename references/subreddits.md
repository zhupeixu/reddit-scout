# 推荐 Subreddit 列表

## 默认配置（脚本内置）

| Subreddit | 排序 | 时间段 | 适合发现 |
|-----------|------|--------|---------|
| r/BuyItForLife | top | week | 耐用品、质量痛点 |
| r/Frugal | top | week | 省钱需求、高频消耗品 |
| r/HomeImprovement | hot | — | 家居工具、改造项目 |
| r/malelivingspace | top | week | 男性居家产品、家居美学 |
| r/dogs | hot | — | 宠物用品 |
| r/AskReddit | hot | — | 广泛痛点、通用产品 |

## 按品类扩展

### 厨房 / 食品
- r/Cooking, r/MealPrepSunday, r/cookingforbeginners
- r/Baking, r/Coffee, r/tea

### 健身 / 户外
- r/Fitness, r/xxfitness, r/running
- r/camping, r/hiking, r/ultralight
- r/HydroHomies (水壶/饮水习惯)

### 家居 / 收纳
- r/organization, r/declutter, r/CleaningTips
- r/InteriorDesign, r/DIY

### 宠物
- r/cats, r/dogs, r/puppy101
- r/reptiles, r/hamster

### 育儿
- r/Parenting, r/beyondthebump, r/NewParents
- r/BabyBumps, r/daddit

### 工作 / 生产力
- r/pcmasterrace, r/mechanicalkeyboards
- r/ArtificialIntelligence (用AI辅助工作的痛点)
- r/digitalnomad, r/WorkFromHome

### 时尚 / 美妆
- r/femalefashionadvice, r/malefashionadvice
- r/SkincareAddiction, r/Frugal_beauty
- r/weddingplanning

### 旅行 / 行李
- r/travel, r/solotravel, r/backpacking
- r/onebag (极简行李流)

### 高购买力用户
- r/BuyItForLife (强烈推荐，用户接受高溢价)
- r/audiophile, r/cameras
- r/woodworking, r/knives

## 选品侦察高效 Subreddit 组合

### 组合A：家居刚需（默认）
BuyItForLife + Frugal + HomeImprovement + malelivingspace

### 组合B：宠物市场
dogs + cats + puppy101 + BuyItForLife

### 组合C：健身/户外
Fitness + running + camping + HydroHomies + BuyItForLife

### 组合D：厨房/食品
Cooking + MealPrepSunday + Frugal + BuyItForLife

### 组合E：育儿用品
Parenting + beyondthebump + NewParents + BuyItForLife

## 调整脚本中的 SUBREDDITS 变量

```python
SUBREDDITS = [
    ("BuyItForLife", "top", "week"),   # (版块名, 排序方式, 时间段)
    ("Frugal", "top", "week"),
    ("dogs", "hot", None),             # hot 排序不需要时间段
]
```

- 排序方式：`top`（高赞）、`hot`（热门）、`new`（最新）
- 时间段（top 时有效）：`hour`、`day`、`week`、`month`、`year`、`all`
