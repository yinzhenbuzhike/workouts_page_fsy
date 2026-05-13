interface ISiteMetadataResult {
  siteTitle: string;
  siteUrl: string;
  description: string;
  keywords: string;
  logo: string;
  navLinks: {
    name: string;
    url: string;
  }[];
}

const data: ISiteMetadataResult = {
  siteTitle: '晚风踏星河的运动记录',
  siteUrl: 'https://blog.4a1801.life',
  logo: 'https://pan.4a1801.life:11443/d/public/logo1.jpg',
  description: 'Personal site and blog',
  keywords: 'workouts, running, cycling, riding, roadtrip, hiking, swimming',
  navLinks: [ ],// 清空导航链接
};

export default data;
