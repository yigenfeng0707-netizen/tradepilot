import { motion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { type ComponentType, type SVGProps } from 'react';

interface LucideProps extends SVGProps<SVGSVGElement> {
  size?: number | string;
}

interface CategoryCardProps {
  icon: ComponentType<LucideProps>;
  name: string;
  description: string;
  /** 该品类在后端是否有内置检索词（能直接跑真实扫描）。
   *  以前这里显示的是写死的增长率 +23.5% 之类——没有数据源，纯属编造。 */
  hasRealSource?: boolean;
}

export default function CategoryCard({
  icon: Icon,
  name,
  description,
  hasRealSource,
}: CategoryCardProps) {
  const navigate = useNavigate();

  return (
    <motion.div
      whileHover={{ scale: 1.03, y: -4 }}
      whileTap={{ scale: 0.98 }}
      onClick={() => navigate(`/market-insight?category=${encodeURIComponent(name)}`)}
      className="glass-light cursor-pointer rounded-xl p-4 transition-colors hover:border-jade-500/30"
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-jade-500/10 text-jade-400">
          <Icon size={20} />
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
            hasRealSource
              ? 'bg-jade-500/10 text-jade-400'
              : 'bg-white/5 text-gray-500'
          }`}
        >
          {hasRealSource ? '可跑真实数据' : '需自填关键词'}
        </span>
      </div>
      <h3 className="text-sm font-medium text-white mb-1">{name}</h3>
      <p className="text-xs text-gray-500 line-clamp-2">{description}</p>
    </motion.div>
  );
}
